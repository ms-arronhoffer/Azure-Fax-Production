import copy
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from azure.core.exceptions import ResourceNotFoundError
from PIL import Image
from pypdf import PdfReader, PdfWriter

from pipeline.documents import normalize
from pipeline.engine import Engine
from pipeline.policy import Conflict, Rejected, fingerprint
from pipeline.storage import Store, encode, now
from tests.test_policy import OWNER, config
from tests.v2_helpers import config as v2_config, extraction, classification

FAX = "33333333-3333-4333-8333-333333333333"


class Lease:
    def renew(self):
        pass


class Clock:
    def __init__(self):
        self.value = datetime.fromisoformat("2030-01-01T00:00:00+00:00")

    def now(self):
        return self.value.isoformat()

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class MemoryStore(Store):
    """Explicit offline fake; no Azure auth/network or shared user environment."""

    def __init__(self):
        self.objects, self.jobs, self.messages = {}, {}, []
        self.commit_times = {}
        self.clock = now
        self.fail_name = None
        self.enqueue_failure = False
        self.status_failure = False

    def create_job(self, job):
        if job["faxId"] in self.jobs:
            return False
        self.jobs[job["faxId"]] = copy.deepcopy(job)
        return True

    def job(self, fax):
        return copy.deepcopy(self.jobs[fax])

    @contextmanager
    def lock(self, fax):
        yield Lease()

    def save_job(self, job, lease):
        if self.status_failure and job["status"] == "Completed":
            raise RuntimeError("injected_status_failure")
        self.jobs[job["faxId"]] = copy.deepcopy(job)

    def read(self, container, name):
        if (container, name) not in self.objects:
            raise ResourceNotFoundError()
        return self.objects[(container, name)]

    def exists(self, container, name):
        return (container, name) in self.objects

    def read_bounded(self, container, name, maximum):
        data = self.read(container, name)
        if len(data) > maximum:
            raise Rejected("invalid_upload_size")
        return data

    def commit_time(self, container, name):
        self.read(container, name)
        return self.commit_times[(container, name)]

    def immutable(self, container, name, data, mime="application/json"):
        import hashlib
        if name == self.fail_name:
            raise RuntimeError("injected_write_failure")
        key = (container, name)
        if key in self.objects and self.objects[key] != data:
            raise Conflict()
        if key not in self.objects:
            self.commit_times[key] = self.clock()
        self.objects[key] = data
        return {"container": container, "name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def enqueue(self, fax):
        if self.enqueue_failure:
            raise RuntimeError("injected_enqueue_failure")
        self.messages.append(fax)


def pdf(pages=2):
    writer, stream = PdfWriter(), BytesIO()
    for index in range(pages):
        writer.add_blank_page(width=100 + index, height=100)
    writer.write(stream)
    return stream.getvalue()


class FakeAI:
    def preflight(self, c):
        pass


class PipelineTests(unittest.TestCase):
    def test_tiff_normalization_is_retry_stable(self):
        with Image.new("RGB", (10, 10), "white") as image:
            stream = BytesIO()
            image.save(stream, format="TIFF")
        first, _ = normalize(stream.getvalue(), "image/tiff", 100)
        second, _ = normalize(stream.getvalue(), "image/tiff", 100)
        self.assertEqual(first, second)
        metadata = PdfReader(BytesIO(first)).metadata
        self.assertEqual(metadata.creation_date.year, 1970)
        self.assertEqual(metadata.modification_date.year, 1970)

    def setUp(self):
        self.store = MemoryStore()
        self.original = pdf()
        self.meta = {"owner": OWNER, "source": "clinic-a", "mime": "application/pdf",
                     "fingerprint": fingerprint(self.original, "clinic-a", "application/pdf", OWNER)}
        self.store.intake(FAX, self.original, self.meta, v2_config())
        self.engine = Engine(self.store, FakeAI())

    def ready_extract(self, confidence=.8):
        self.engine.prepare(FAX)
        classify = {"documents": [
            {"docType": "cover", "confidence": .99, "boundingRegions": [{"pageNumber": 1}]},
            {"docType": "body", "confidence": .99, "boundingRegions": [{"pageNumber": 2}]}]}
        self.store.immutable_json("working", FAX + "/classify-result.json", classify)
        self.engine.remove_covers(FAX)
        self.store.immutable_json("working", FAX + "/type-result.json", classification("exam"))
        self.engine.decide_type(FAX)
        self.store.immutable_json("working", FAX + "/extract-result.json", extraction(confidence=confidence))

    def test_duplicate_and_mismatch(self):
        self.store.intake(FAX, self.original, self.meta, config())
        self.assertEqual(len(self.store.jobs), 1)
        for changed in ({"fingerprint": "different-body"}, {"fingerprint": "different-source"}):
            with self.assertRaises(Conflict):
                self.store.intake(FAX, self.original, {**self.meta, **changed}, config())
        self.assertEqual(self.store.read("incoming", FAX + "/original"), self.original)

    def test_enqueue_failure_retriable_idempotent(self):
        self.store.enqueue_failure = True
        with self.assertRaises(RuntimeError):
            self.store.intake(FAX, self.original, self.meta, config())
        self.assertTrue(self.store.job(FAX)["originalReady"])
        self.assertEqual(self.store.job(FAX)["status"], "Accepted")
        self.store.enqueue_failure = False
        self.store.intake(FAX, self.original, self.meta, config())
        self.assertEqual(len(self.store.jobs), 1)

    def test_cover_physically_removed_and_original_preserved(self):
        self.ready_extract()
        body = PdfReader(BytesIO(self.store.read("working", FAX + "/body.pdf")))
        self.assertEqual(len(body.pages), 1)
        self.assertEqual(float(body.pages[0].mediabox.width), 101)
        self.assertEqual(self.store.read("incoming", FAX + "/original"), self.original)

    def test_prepare_checkpoint_restores_phase_facts_after_status_failure(self):
        save = self.store.save_job
        def fail_processing(job, lease):
            if job["status"] == "Processing":
                raise RuntimeError("offline_prepare_status_failure")
            save(job, lease)
        with patch.object(self.store, "save_job", side_effect=fail_processing):
            with self.assertRaises(RuntimeError):
                self.engine.prepare(FAX)
        timestamp = self.store.json("working", FAX + "/timing-prepare-completedAt.json")["at"]
        self.engine.prepare(FAX)
        job = self.store.job(FAX)
        self.assertEqual(job["pageCount"], 2)
        self.assertEqual(job["status"], "Processing")
        self.assertEqual(job["phaseTimings"]["prepare"]["completedAt"], timestamp)

    def test_fanout_manifest_and_retry_idempotence(self):
        self.ready_extract()
        result = self.engine.publish(FAX)
        self.assertEqual(result, {"state": "completed"})
        manifest = self.store.json("extracted", FAX + "/manifest.json")
        self.assertEqual(len(manifest["artifacts"]), 8)
        before = copy.deepcopy(self.store.objects)
        self.engine.publish(FAX)
        self.assertEqual(before, self.store.objects)
        self.assertNotIn("sensitive", json.dumps(result))

    def test_rejection_isolation_and_scores(self):
        self.ready_extract(.7999)
        self.engine.publish(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "PendingHumanReview")
        self.assertFalse(any(container in {"extracted", "routed"} for container, _ in self.store.objects))
        review = self.store.json("human-review", FAX + "/review.json")
        self.assertEqual(review["gate"]["scores"]["PatientId"], [.7999])
        self.assertEqual(review["originalPageMap"]["bodyPages"], [2])
        self.assertEqual(self.store.read("human-review", FAX + "/original"), self.original)
        self.engine.fail(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "PendingHumanReview")

    def test_partial_failure_no_commit_and_safe_terminal(self):
        self.ready_extract()
        self.store.fail_name = f"billing/{FAX}/content.json"
        with self.assertRaises(RuntimeError):
            self.engine.publish(FAX)
        self.assertFalse(self.store.exists("extracted", FAX + "/manifest.json"))
        self.engine.fail(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "Failed")
        self.assertTrue(self.store.json("human-review", FAX + "/review.json")["partialPublication"])
        self.store.fail_name = None
        self.engine.publish(FAX)
        self.assertFalse(self.store.exists("extracted", FAX + "/manifest.json"))

    def test_partial_failure_retry_completes(self):
        self.ready_extract()
        self.store.fail_name = f"billing/{FAX}/content.json"
        with self.assertRaises(RuntimeError):
            self.engine.publish(FAX)
        self.store.fail_name = None
        self.engine.publish(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "Completed")

    def test_manifest_commit_status_repair(self):
        self.ready_extract()
        self.store.status_failure = True
        with self.assertRaises(RuntimeError):
            self.engine.publish(FAX)
        self.assertTrue(self.store.exists("extracted", FAX + "/manifest.json"))
        report_before = self.store.read("extracted", FAX + "/report.json")
        self.assertIn(FAX + "/report.json", [
            artifact["name"] for artifact in self.store.json("extracted", FAX + "/manifest.json")["artifacts"]])
        self.store.status_failure = False
        self.engine.fail(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "Completed")
        self.assertFalse(self.store.exists("human-review", FAX + "/review.json"))
        self.assertEqual(report_before, self.store.read("extracted", FAX + "/report.json"))

    def test_auto_report_failure_before_write_is_not_terminal(self):
        self.ready_extract()
        self.store.fail_name = FAX + "/report.json"
        with self.assertRaises(RuntimeError):
            self.engine.publish(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "Publishing")
        self.assertFalse(self.store.exists("extracted", FAX + "/manifest.json"))
        self.store.fail_name = None
        self.engine.publish(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "Completed")

    def test_auto_report_ambiguous_write_is_retry_stable(self):
        self.ready_extract()
        write = self.store.immutable
        once = [True]
        def fail_after_write(container, name, data, mime="application/json"):
            result = write(container, name, data, mime)
            if name == FAX + "/report.json" and once[0]:
                once[0] = False
                raise RuntimeError("offline_ambiguous_report_write")
            return result
        with patch.object(self.store, "immutable", side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):
                self.engine.publish(FAX)
            before = self.store.read("extracted", FAX + "/report.json")
            self.assertFalse(self.store.exists("extracted", FAX + "/manifest.json"))
            self.engine.publish(FAX)
        self.assertEqual(before, self.store.read("extracted", FAX + "/report.json"))

    def test_auto_completion_clock_includes_delayed_writes_and_survives_status_retry(self):
        self.ready_extract()
        clock = Clock()
        self.store.clock = clock.now
        start = clock.value
        write = self.store.immutable
        def delayed_write(container, name, data, mime="application/json"):
            if not self.store.exists(container, name):
                if name == f"billing/{FAX}/content.hl7":
                    clock.advance(300)
                elif name == f"{FAX}/report.json":
                    clock.advance(30)
                elif name == f"{FAX}/manifest.json":
                    clock.advance(10)
            return write(container, name, data, mime)
        self.store.status_failure = True
        with patch("pipeline.engine.now", side_effect=clock.now), \
                patch.object(self.store, "immutable", side_effect=delayed_write):
            with self.assertRaises(RuntimeError):
                self.engine.publish(FAX)
            report = self.store.json("extracted", f"{FAX}/report.json")
            report_bytes = self.store.read("extracted", f"{FAX}/report.json")
            self.assertNotIn("completedAt", report)
            self.assertEqual(datetime.fromisoformat(report["completionPreparedAt"]), start + timedelta(seconds=300))
            self.assertEqual(report["snapshotPhase"], "completion-prepared")
            self.assertEqual(report["completionClock"]["property"], "lastModified")
            clock.advance(600)
            self.store.status_failure = False
            self.engine.fail(FAX)
        job = self.store.job(FAX)
        self.assertEqual(datetime.fromisoformat(job["completedAt"]), start + timedelta(seconds=340))
        self.assertEqual(job["completedAtSource"], "manifestLastModified")
        self.assertGreater(job["completedAt"], job["publicationStartedAt"])
        self.assertEqual(report_bytes, self.store.read("extracted", f"{FAX}/report.json"))

    def test_initial_review_report_failure_is_precommit(self):
        self.ready_extract(.79)
        self.store.fail_name = FAX + "/report.json"
        with self.assertRaises(RuntimeError):
            self.engine.publish(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "Processing")
        self.assertFalse(self.store.exists("human-review", FAX + "/review.json"))
        self.store.fail_name = None
        self.engine.publish(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "PendingHumanReview")
        self.assertTrue(self.store.exists("human-review", FAX + "/report.json"))

    def test_review_commit_is_barrier_even_when_status_commit_failed(self):
        self.ready_extract()
        self.store.fail_name = f"billing/{FAX}/content.json"
        with self.assertRaises(RuntimeError):
            self.engine.publish(FAX)
        save = self.store.save_job
        def fail_status(job, lease):
            if job["status"] == "Failed":
                raise RuntimeError("injected_review_status_failure")
            save(job, lease)
        with patch.object(self.store, "save_job", side_effect=fail_status):
            with self.assertRaises(RuntimeError):
                self.engine.fail(FAX)
        self.assertTrue(self.store.exists("human-review", FAX + "/review.json"))
        self.store.fail_name = None
        self.engine.publish(FAX)
        self.assertEqual(self.store.job(FAX)["status"], "Failed")
        self.assertFalse(self.store.exists("extracted", FAX + "/manifest.json"))

    def test_unreadable_original_review(self):
        self.store.objects[("working", FAX + "/admitted-original")] = b"%PDF-broken"
        self.assertEqual(self.engine.prepare(FAX), {"state": "terminal"})
        self.assertEqual(self.store.job(FAX)["reason"], "unreadable_document")

    def test_tiff_bounds_and_page_conversion(self):
        image = Image.new("RGB", (20, 20), "white")
        stream = BytesIO()
        image.save(stream, format="TIFF", save_all=True, append_images=[image])
        result, count = normalize(stream.getvalue(), "image/tiff", 2)
        self.assertEqual(count, 2)
        self.assertEqual(len(PdfReader(BytesIO(result)).pages), 2)
        with self.assertRaises(Rejected):
            normalize(stream.getvalue(), "image/tiff", 1)
        with self.assertRaises(Rejected):
            normalize(self.original, "image/tiff", 100)


if __name__ == "__main__":
    unittest.main()
