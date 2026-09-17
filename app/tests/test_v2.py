import asyncio
import copy
import hashlib
import inspect
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import azure.functions as func

import function_app
from pipeline.contracts import canonical, classify_body, review_request, select_profile, validate_v2, validated_values
from pipeline.engine import Engine
from pipeline.intelligence import Intelligence
from pipeline.hl7 import build, escape, validate_message
from pipeline.ingestion import ingest, manifest, record_rejection, strict_json
from pipeline.policy import Actor, Conflict, Rejected, validate_config
from pipeline.reports import facts
from pipeline import runtime
from pipeline.storage import encode
from tests.test_api import user_function
from tests.test_pipeline import FAX, FakeAI, MemoryStore, Clock, pdf
from tests import test_pipeline as pipeline_tests
from tests.test_policy import OID, OWNER, TENANT, principal
from tests.v2_helpers import classification, config, extraction, values


class ContractsTests(unittest.TestCase):
    def test_configuration(self):
        validate_config(config())
        for change in ("missing-calibration", "threshold", "classes", "fields", "types", "uploader", "source"):
            c = config()
            if change == "missing-calibration": c["faxTypeClassifier"]["calibrationId"] = ""
            if change == "threshold": c["faxTypeClassifier"]["minimumConfidence"] = .7999
            if change == "classes": c["faxTypeClassifier"]["classes"]["unknown"] = "exam"
            if change == "fields": c["documentTypes"]["rx"]["expectedFields"].pop()
            if change == "types": c["documentTypes"].pop("exam")
            if change == "uploader": c["directUpload"]["trustedUploader"] = "not-an-oid"
            if change == "source": c["directUpload"]["allowedSources"] = ["../escape"]
            with self.subTest(change=change), self.assertRaises(Rejected):
                validate_v2(c)

    def test_legacy_configuration_rejected_at_real_admission_before_services(self):
        from tests.test_policy import config as legacy_config
        runtime.admission_config.cache_clear()
        self.addCleanup(runtime.admission_config.cache_clear)
        with patch.object(runtime.Path, "read_text", return_value=json.dumps(legacy_config())), \
                patch.object(runtime, "services") as services:
            with self.assertRaisesRegex(Rejected, "^configuration_upgrade_required$"):
                runtime.admission_config()
        services.assert_not_called()

    def test_type_boundary_unknown_mixed_missing_and_whole_body(self):
        c = config()["faxTypeClassifier"]
        self.assertEqual(classify_body(classification("exam", 2), 2, c)["documentType"], "exam")
        for result in (classification("unknown"), classification("exam", confidence=.7999),
                       classification("exam", confidence=True), classification("exam", confidence=float("nan")),
                       classification("exam", confidence=None), {"documents": []},
                       {"documents": classification("exam")["documents"] + classification("rx")["documents"]},
                       classification("exam", count=2)):
            with self.subTest(result=result), self.assertRaises(Rejected):
                classify_body(result, 1, c)

    def test_typed_values_reject_false_numeric_text_and_preserve_zero_dose(self):
        v = values()
        for field in v:
            for invalid in (False, 0, 1.5):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(Rejected):
                    validated_values("exam", {**v, field: invalid})
        for invalid in (None, "", " ", [], {}, float("nan")):
            with self.subTest(invalid=invalid), self.assertRaises(Rejected):
                validated_values("exam", {**v, "ResultText": invalid})
        with self.assertRaises(Rejected):
            validated_values("exam", {})
        with self.assertRaises(Rejected):
            validated_values("exam", {**v, "DateOfBirth": "2026-02-30"})
        for zero in (0, 0.0, "0", "0.0"):
            self.assertEqual(validated_values("rx", {**values("rx"), "Dose": zero})["Dose"], zero)
        for bad in (False, -1, "-1", "10 mg", "ZERO", None, "", float("nan"), float("inf")):
            with self.subTest(dose=bad), self.assertRaises(Rejected):
                validated_values("rx", {**values("rx"), "Dose": bad})
        corrected = validated_values("exam", {
            **v, "PatientId": "TEST0 101", "PatientName": "TEST PERSON0 101",
            "EncounterId": "ENCOUNTER0 101", "ProviderId": "PROVIDER0 101"})
        self.assertEqual(corrected["PatientId"], "TEST0101")
        self.assertEqual(corrected["PatientName"], "TEST PERSON0101")
        self.assertEqual(corrected["EncounterId"], "ENCOUNTER0101")
        self.assertEqual(corrected["ProviderId"], "PROVIDER0101")
        self.assertEqual(validated_values("exam", {**v, "ResultText": "Grade 1 2 finding"})[
            "ResultText"], "Grade 1 2 finding")

    def test_profile_source_and_not_applicable_fail_closed(self):
        for mutate in ("missing", "wrong-type", "source", "production", "invalid-na"):
            c = config()
            if mutate == "missing": c["hl7Profiles"] = {}
            if mutate == "wrong-type": c["hl7Profiles"]["clinic-a"]["exam"]["profile"] = "demo-rde-o11-v1"
            if mutate == "source": c["hl7Profiles"]["clinic-a"]["exam"]["sendingFacility"] = "x|PID"
            if mutate == "production": c["hl7Profiles"]["clinic-a"]["exam"]["processingId"] = "P"
            if mutate == "invalid-na": c["hl7Profiles"]["clinic-a"]["exam"] = {"status": "NotApplicable"}
            with self.subTest(mutate=mutate), self.assertRaises(Rejected):
                select_profile(c, "clinic-a", "exam")
        c["hl7Profiles"]["clinic-a"]["exam"] = {"status": "NotApplicable", "reason": "explicit synthetic JSON-only lane"}
        self.assertEqual(select_profile(c, "clinic-a", "exam")["status"], "NotApplicable")

    def test_all_hl7_demo_profiles_and_escaping(self):
        for kind, message in (("exam", b"ORU^R01"), ("order", b"ORM^O01"), ("rx", b"RDE^O11")):
            record = canonical({"faxId": FAX, "source": "clinic-a"}, kind, values(kind), {}, "Automatic")
            data, disposition = build(config(), record)
            self.assertIn(message, data)
            self.assertIn(b"|T|2.5.1", data)
            self.assertEqual(disposition["status"], "Validated")
        self.assertEqual(escape("x|^~&\\\r\n"), "x\\F\\\\S\\\\R\\\\T\\\\E\\\\X0D\\\\X0A\\")
        record = canonical({"faxId": FAX, "source": "clinic-a"}, "exam",
                           {**values(), "ResultText": "hello\rMSH|evil"}, {}, "Automatic")
        data, _ = build(config(), record)
        self.assertNotIn(b"\rMSH|evil", data)
        with self.assertRaises(Rejected):
            validate_message(data.decode().replace("|T|2.5.1", "|P|2.5.1"),
                             "exam", config()["hl7Profiles"]["clinic-a"]["exam"], "wrong")


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.store, self.body = MemoryStore(), pdf()
        self.ready = {"schemaVersion": 1, "faxId": FAX, "source": "clinic-a", "mime": "application/pdf",
                      "original": {"container": "incoming", "name": f"{FAX}/original"},
                      "sha256": hashlib.sha256(self.body).hexdigest()}

    def call(self, value=None, maximum=20971520):
        return ingest(self.store, encode(value or self.ready), f"ready/{FAX}.json", config(), maximum)

    def event(self, event_type="Microsoft.Storage.BlobCreated", subject=None, url=None):
        event = Mock()
        event.event_type = event_type
        event.subject = subject or f"/blobServices/default/containers/ready/blobs/{FAX}.json"
        event.get_json.return_value = {
            "url": url or f"https://data.blob.core.windows.net/ready/{FAX}.json"
        }
        return event

    def test_missing_original_never_starts_and_valid_replay(self):
        with self.assertRaises(Exception):
            self.call()
        self.assertFalse(self.store.jobs or self.store.messages)
        self.store.immutable("incoming", f"{FAX}/original", self.body)
        self.call()
        self.call()
        self.assertEqual(len(self.store.jobs), 1)
        self.assertTrue(self.store.job(FAX)["admissionCommitted"])
        self.assertEqual(self.store.original(FAX), self.body)
        self.assertIn("not end-user", self.store.job(FAX)["provenance"]["identityMeaning"])

    def test_invalid_manifests_and_hash_do_not_create_jobs(self):
        self.store.immutable("incoming", f"{FAX}/original", self.body)
        for change in ({"sha256": "0" * 64}, {"source": "untrusted"}, {"faxId": "../bad"},
                       {"original": {"container": "incoming", "name": "../original"}},
                       {"original": {"url": "https://evil.example"}}, {"endUserOid": OID},
                       {"schemaVersion": True}, {"mime": "text/plain"}):
            with self.subTest(change=change), self.assertRaises(Rejected):
                self.call({**self.ready, **change})
            self.assertFalse(self.store.jobs or self.store.messages)
        with self.assertRaises(Rejected):
            self.call(maximum=2)
        with self.assertRaises(Rejected):
            manifest(encode(self.ready), "ready/other.json", config())
        with self.assertRaises(Rejected):
            strict_json(b'{"faxId":"a","faxId":"b"}')

    def test_conflict_and_snapshot_survive_uploader_mutation(self):
        self.store.immutable("incoming", f"{FAX}/original", self.body)
        self.call()
        self.store.objects[("incoming", f"{FAX}/original")] = pdf(3)
        changed = {**self.ready, "sha256": hashlib.sha256(pdf(3)).hexdigest()}
        with self.assertRaises(Conflict):
            self.call(changed)
        self.assertEqual(self.store.original(FAX), self.body)

    def test_queue_guard_on_uncommitted_original(self):
        self.store.jobs[FAX] = {"faxId": FAX, "status": "Accepted", "originalReady": False}
        starter = inspect.unwrap(user_function(function_app.queue_starter))
        client = AsyncMock()
        with patch.object(function_app, "processing_enabled", return_value=True), \
                patch.object(function_app, "services", return_value=(self.store, None)):
            with self.assertRaises(RuntimeError):
                asyncio.run(starter(func.QueueMessage(body=encode({"faxId": FAX})), client))
        client.start_new.assert_not_called()

    def test_rejected_trigger_records_observable_immutable_attempt_not_fake_status(self):
        self.store.immutable("incoming", f"{FAX}/original", self.body)
        raw = encode({**self.ready, "sha256": "0" * 64})
        self.store.immutable("ready", f"{FAX}.json", raw)
        digest = hashlib.sha256(raw).hexdigest()
        endpoint = inspect.unwrap(user_function(function_app.ready_manifest))
        with patch.dict(os.environ, {"DATA_STORAGE_ACCOUNT_URL": "https://data.blob.core.windows.net"}), \
                patch.object(function_app, "processing_enabled", return_value=True), \
                patch.object(function_app, "admission_config", return_value=config()), \
                patch.object(function_app, "services", return_value=(self.store, None)):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "^ready_admission_failed$"):
                    asyncio.run(endpoint(self.event(), AsyncMock()))
        self.assertFalse(self.store.jobs or self.store.messages)
        evidence = self.store.json("jobs", f"_admission-rejections/{FAX}/{digest}.json")
        self.assertEqual(evidence["reason"], "original_digest_mismatch")
        self.assertEqual(evidence["status"], "RejectedAttempt")
        self.assertNotIn("source", evidence)
        headers, env = principal()
        request = func.HttpRequest("GET", "https://private.example/api/faxes/x/admissions/y",
                                   headers=headers, body=b"",
                                   route_params={"faxId": FAX, "manifestSha256": digest})
        get = user_function(function_app.get_admission_attempt)
        with patch.dict(os.environ, env, clear=True), \
                patch.object(function_app, "services", return_value=(self.store, None)):
            response = get(request)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("configuredIntegration", json.loads(response.get_body()))
            with patch.object(function_app, "authenticate", return_value=Actor(TENANT, FAX, frozenset({"Fax.Submit"}))):
                self.assertEqual(get(request).status_code, 404)

    def test_event_grid_starts_durable_directly_without_queue(self):
        self.store.immutable("incoming", f"{FAX}/original", self.body)
        self.store.immutable("ready", f"{FAX}.json", encode(self.ready))
        client = AsyncMock()
        client.get_status.return_value = None
        endpoint = inspect.unwrap(user_function(function_app.ready_manifest))
        with patch.dict(os.environ, {"DATA_STORAGE_ACCOUNT_URL": "https://data.blob.core.windows.net"}), \
                patch.object(function_app, "processing_enabled", return_value=True), \
                patch.object(function_app, "admission_config", return_value=config()), \
                patch.object(function_app, "services", return_value=(self.store, Mock())):
            asyncio.run(endpoint(self.event(), client))
            asyncio.run(endpoint(self.event(), client))
        client.start_new.assert_awaited_once_with(
            "fax_pipeline", instance_id=FAX, client_input={"faxId": FAX})
        client.get_status.assert_awaited_once_with(FAX)
        self.assertTrue(self.store.jobs[FAX]["orchestrationStarted"])
        self.assertIn("orchestrationIntentAt", self.store.jobs[FAX])
        self.assertEqual(self.store.messages, [])

    def test_event_grid_rejects_untrusted_envelopes_before_blob_read(self):
        endpoint = inspect.unwrap(user_function(function_app.ready_manifest))
        invalid = [
            self.event(event_type="Microsoft.Storage.BlobDeleted"),
            self.event(subject=f"/blobServices/default/containers/incoming/blobs/{FAX}.json"),
            self.event(url=f"https://evil.example/ready/{FAX}.json"),
            self.event(url=f"https://data.blob.core.windows.net/ready/{FAX}.json?sig=secret"),
        ]
        with patch.dict(os.environ, {"DATA_STORAGE_ACCOUNT_URL": "https://data.blob.core.windows.net"}), \
                patch.object(function_app, "processing_enabled", return_value=True):
            for event in invalid:
                with self.subTest(event=event), \
                        self.assertRaisesRegex(RuntimeError, "^ready_admission_failed$"):
                    asyncio.run(endpoint(event, AsyncMock()))


class ReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = pipeline_tests.PipelineTests()
        fixture.setUp()
        fixture.ready_extract(.79)
        self.store, self.engine = fixture.store, fixture.engine
        self.engine.publish(FAX)
        self.actor = Actor(TENANT, OID, frozenset({"Fax.Review"}))
        self.body = {"documentType": "exam", "bodyPages": [2], "values": values(), "reason": "independent synthetic inspection"}

    def test_human_release_preserves_scores_and_immutable_revision(self):
        result = self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.assertEqual(result["status"], "Completed")
        evidence = self.store.json("human-review", f"{FAX}/revisions/000001/decision.json")
        self.assertEqual(evidence["originalEvidence"]["gate"]["scores"]["PatientId"], [.79])
        self.assertEqual(evidence["reviewer"]["oid"], OID)
        manifest = self.store.json("extracted", f"{FAX}/revisions/000001/manifest.json")
        self.assertFalse(manifest["gatePassed"])
        self.assertTrue(manifest["reviewAuthorized"])
        self.assertFalse(self.store.exists("extracted", FAX + "/manifest.json"))
        with self.assertRaises(Conflict):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')

    def test_review_validation_authorization_and_mapping(self):
        with self.assertRaises(PermissionError):
            self.engine.review(FAX, Actor(TENANT, OID, frozenset({"Fax.Admin"})), self.body, '"review-0"')
        for changed in ({"bodyPages": []}, {"bodyPages": [2, 2]}, {"bodyPages": [True]},
                        {"bodyPages": [3]}, {"values": {}}, {"reason": ""}, {"documentType": "unknown"}):
            with self.subTest(changed=changed), self.assertRaises(Rejected):
                self.engine.review(FAX, self.actor, {**self.body, **changed}, '"review-0"')
        self.store.jobs[FAX]["config"]["hl7Profiles"] = {}
        with self.assertRaises(Rejected):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.assertFalse(any(c == "extracted" for c, _ in self.store.objects))

    def test_review_corrects_type_and_plan(self):
        self.store.jobs[FAX]["modelIds"] = {"extractor": "mock-exam"}
        result = self.engine.review(FAX, self.actor, {**self.body, "documentType": "order",
                                    "bodyPages": [1, 2], "values": values("order")}, '"review-0"')
        self.assertEqual(result["completionMode"], "Reviewed")
        record = self.store.json("extracted", f"{FAX}/revisions/000001/canonical.json")
        self.assertEqual(record["documentType"], "order")
        self.assertEqual(record["originalPageMap"]["coverPages"], [])
        report = self.store.json("extracted", f"{FAX}/revisions/000001/report.json")
        self.assertEqual(report["modelIds"]["extractor"], "mock-exam")
        self.assertEqual(report["configuredModelIds"]["extractor"], "mock-order")
        self.assertEqual(report["hl7Profile"], "demo-orm-o01-v1")
        self.assertIn("decisionAt", report["phaseTimings"]["review"])

    def test_review_explicit_not_applicable_is_json_only(self):
        self.store.jobs[FAX]["config"]["hl7Profiles"]["clinic-a"]["exam"] = {
            "status": "NotApplicable", "reason": "synthetic JSON-only lane"}
        self.engine.review(FAX, self.actor, self.body, '"review-0"')
        manifest = self.store.json("extracted", f"{FAX}/revisions/000001/manifest.json")
        self.assertEqual(manifest["hl7Disposition"]["status"], "NotApplicable")
        self.assertFalse(any(name.endswith(".hl7") for _, name in self.store.objects))

    def test_partial_revision_retry_and_optimistic_conflict(self):
        self.store.fail_name = f"billing/{FAX}/revisions/000001/content.json"
        with self.assertRaises(RuntimeError):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.assertFalse(self.store.exists("extracted", f"{FAX}/revisions/000001/manifest.json"))
        self.store.fail_name = None
        with self.assertRaises(Conflict):
            self.engine.review(FAX, self.actor, {**self.body, "reason": "different"}, '"review-0"')
        self.assertEqual(self.engine.review(FAX, self.actor, self.body, '"review-0"')["status"], "Completed")

    def test_review_status_write_recovery(self):
        self.store.status_failure = True
        with self.assertRaises(RuntimeError):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        report_name = f"{FAX}/revisions/000001/report.json"
        report_before = self.store.read("extracted", report_name)
        manifest = self.store.json("extracted", f"{FAX}/revisions/000001/manifest.json")
        self.assertIn(report_name, [artifact["name"] for artifact in manifest["artifacts"]])
        self.store.status_failure = False
        self.assertEqual(self.engine.review(FAX, self.actor, self.body, '"review-0"')["status"], "Completed")
        self.assertEqual(report_before, self.store.read("extracted", report_name))

    def test_review_report_failure_is_precommit_and_retry_stable(self):
        name = f"{FAX}/revisions/000001/report.json"
        write = self.store.immutable
        once = [True]
        def fail_after_write(container, target, data, mime="application/json"):
            result = write(container, target, data, mime)
            if target == name and once[0]:
                once[0] = False
                raise RuntimeError("offline_ambiguous_report_write")
            return result
        with patch.object(self.store, "immutable", side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):
                self.engine.review(FAX, self.actor, self.body, '"review-0"')
            before = self.store.read("extracted", name)
            self.assertEqual(self.store.job(FAX)["status"], "PendingHumanReview")
            self.assertFalse(self.store.exists("extracted", f"{FAX}/revisions/000001/manifest.json"))
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.assertEqual(before, self.store.read("extracted", name))

    def test_review_completion_clock_is_manifest_commit_not_reviewer_decision(self):
        clock = Clock()
        self.store.clock = clock.now
        start = clock.value
        root = f"{FAX}/revisions/000001"
        write = self.store.immutable
        def delayed_write(container, name, data, mime="application/json"):
            if not self.store.exists(container, name):
                if name == f"billing/{root}/content.hl7":
                    clock.advance(300)
                elif name == root + "/report.json":
                    clock.advance(30)
                elif name == root + "/manifest.json":
                    clock.advance(10)
            return write(container, name, data, mime)
        self.store.status_failure = True
        with patch("pipeline.engine.now", side_effect=clock.now), \
                patch.object(self.store, "immutable", side_effect=delayed_write):
            with self.assertRaises(RuntimeError):
                self.engine.review(FAX, self.actor, self.body, '"review-0"')
            report = self.store.json("extracted", root + "/report.json")
            before = self.store.read("extracted", root + "/report.json")
            self.assertNotIn("completedAt", report)
            self.assertEqual(datetime.fromisoformat(report["completionPreparedAt"]), start + timedelta(seconds=300))
            self.assertEqual(datetime.fromisoformat(report["phaseTimings"]["review"]["decisionAt"]), start)
            clock.advance(600)
            self.store.status_failure = False
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        job = self.store.job(FAX)
        self.assertEqual(datetime.fromisoformat(job["completedAt"]), start + timedelta(seconds=340))
        self.assertEqual(job["completedAtSource"], "manifestLastModified")
        self.assertEqual(facts(job)["completedAt"], job["completedAt"])
        self.assertEqual(before, self.store.read("extracted", root + "/report.json"))

    def test_abandon_incomplete_revision_allows_corrected_review_without_reusing_identity(self):
        self.store.fail_name = f"{FAX}/revisions/000001/report.json"
        with self.assertRaises(RuntimeError):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.assertFalse(self.store.exists("extracted", f"{FAX}/revisions/000001/manifest.json"))
        self.store.fail_name = None
        result = self.engine.abandon_review(FAX, self.actor, 1, "Correcting earlier draft", '"review-0"')
        self.assertEqual(result["reviewETag"], '"review-1"')
        with self.assertRaises(Conflict):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        correction = {**self.body, "values": {**values(), "ResultText": "Corrected synthetic text"}}
        result = self.engine.review(FAX, self.actor, correction, '"review-1"')
        self.assertEqual(result["revision"], 2)
        self.assertFalse(self.store.exists("extracted", f"{FAX}/revisions/000001/manifest.json"))
        self.assertTrue(self.store.exists("human-review", f"{FAX}/revisions/000001/abandoned.json"))
        self.assertTrue(self.store.exists("extracted", f"{FAX}/revisions/000002/manifest.json"))

    def test_abandonment_status_failure_cannot_resume_abandoned_revision(self):
        self.store.fail_name = f"{FAX}/revisions/000001/report.json"
        with self.assertRaises(RuntimeError):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.store.fail_name = None
        with patch.object(self.store, "save_job", side_effect=RuntimeError("offline_status_failure")):
            with self.assertRaises(RuntimeError):
                self.engine.abandon_review(FAX, self.actor, 1, "Correcting draft", '"review-0"')
        with self.assertRaises(Conflict):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.engine.abandon_review(FAX, self.actor, 1, "Retry recovery", '"review-0"')
        self.assertEqual(self.engine.review(FAX, self.actor, self.body, '"review-1"')["revision"], 2)

    def test_committed_revision_cannot_be_abandoned(self):
        self.store.status_failure = True
        with self.assertRaises(RuntimeError):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.store.status_failure = False
        with self.assertRaises(Conflict):
            self.engine.abandon_review(FAX, self.actor, 1, "Must not supersede commit", '"review-0"')
        with self.assertRaises(PermissionError):
            self.engine.abandon_review(FAX, Actor(TENANT, OID, frozenset()), 1, "Denied", '"review-0"')
        self.engine.review(FAX, self.actor, self.body, '"review-0"')

    def test_abandon_api_requires_role_and_etag(self):
        self.store.fail_name = f"{FAX}/revisions/000001/report.json"
        with self.assertRaises(RuntimeError):
            self.engine.review(FAX, self.actor, self.body, '"review-0"')
        self.store.fail_name = None
        headers, env = principal(roles=("Fax.Review",))
        env["PROCESSING_ENABLED"] = "true"
        endpoint = user_function(function_app.abandon_review)
        def request(hdrs):
            return func.HttpRequest("POST", "https://private.example/api/faxes/x/review/reservations/1/abandon",
                                    headers=hdrs, body=encode({"reason": "Correcting synthetic draft"}),
                                    route_params={"faxId": FAX, "revision": "1"})
        with patch.dict(os.environ, env, clear=True), \
                patch.object(function_app, "services", return_value=(self.store, self.engine)):
            self.assertEqual(endpoint(request(headers)).status_code, 428)
            unauthorized, _ = principal(roles=("Fax.Admin",))
            self.assertEqual(endpoint(request(unauthorized)).status_code, 403)
            result = endpoint(request({**headers, "If-Match": '"review-0"'}))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["ETag"], '"review-1"')

    def test_authenticated_review_api_and_facts(self):
        endpoint = user_function(function_app.review_fax)
        headers, env = principal(roles=("Fax.Review",))
        env["PROCESSING_ENABLED"] = "true"
        def request(method, hdrs, body=b""):
            return func.HttpRequest(method, "https://private.example/api/faxes/x/review",
                                    headers=hdrs, body=body, route_params={"faxId": FAX})
        with patch.dict(os.environ, env, clear=True), \
                patch.object(function_app, "services", return_value=(self.store, self.engine)):
            get = endpoint(request("GET", headers))
            self.assertEqual(get.status_code, 200)
            self.assertEqual(get.headers["ETag"], '"review-0"')
            self.assertEqual(endpoint(request("POST", headers, encode(self.body))).status_code, 428)
            self.assertEqual(endpoint(request("POST", {**headers, "If-Match": '"bad"'}, encode(self.body))).status_code, 409)
            denied, _ = principal(roles=("Fax.Admin",))
            self.assertEqual(endpoint(request("GET", denied)).status_code, 403)
            self.assertEqual(endpoint(request("POST", {**headers, "If-Match": '"review-0"'}, encode(self.body))).status_code, 200)
        safe = facts(self.store.job(FAX))
        self.assertNotIn("Patient", json.dumps(safe))
        self.assertNotIn("independent synthetic inspection", json.dumps(safe))
        self.assertEqual(safe["fieldMinimumConfidence"], .79)

    def test_original_download_requires_review_role_and_preserves_original_bytes(self):
        endpoint = user_function(function_app.review_original)
        headers, env = principal(roles=("Fax.Review",))
        request = func.HttpRequest("GET", "https://private.example/api/faxes/x/review/original",
                                   headers=headers, body=b"", route_params={"faxId": FAX})
        with patch.dict(os.environ, env, clear=True), \
                patch.object(function_app, "services", return_value=(self.store, self.engine)):
            result = endpoint(request)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.get_body(), self.store.original(FAX))
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertIn("attachment", result.headers["Content-Disposition"])
            with patch.object(function_app, "authenticate", return_value=Actor(TENANT, OID, frozenset({"Fax.Admin"}))):
                self.assertEqual(endpoint(request).status_code, 403)
            del self.store.objects[("human-review", FAX + "/review.json")]
            self.assertEqual(endpoint(request).status_code, 404)


class FullFlowTests(unittest.TestCase):
    def run_pipeline(self, kind="exam", label=None, score=.8, mapping="normal"):
        fixture = pipeline_tests.PipelineTests()
        fixture.setUp()
        store = fixture.store
        if mapping == "missing":
            store.jobs[FAX]["config"]["hl7Profiles"] = {}
        elif mapping == "not-applicable":
            store.jobs[FAX]["config"]["hl7Profiles"]["clinic-a"][kind] = {
                "status": "NotApplicable", "reason": "configured synthetic JSON-only output"}
        starts = []
        class AI:
            def preflight(self, c):
                pass

            def start(self, resource, model, body, split="perPage"):
                starts.append((resource, model, split, body))
                return model

            def poll(self, operation, resource, model):
                if model == "mock-pages":
                    result = {"documents": [
                        {"docType": "cover", "confidence": .8, "boundingRegions": [{"pageNumber": 1}]},
                        {"docType": "body", "confidence": .8, "boundingRegions": [{"pageNumber": 2}]}]}
                elif model == "mock-types":
                    result = classification(label or kind, confidence=score)
                else:
                    result = extraction(kind)
                return {"status": "succeeded", "analyzeResult": result}
        engine = Engine(store, AI())
        self.assertEqual(engine.prepare(FAX), {"state": "ready", "typeClassifier": True})
        for phase in ("classify", "type", "extract"):
            self.assertEqual(engine.ai_step(FAX, phase, "start"), {"state": "running"})
            self.assertEqual(engine.ai_step(FAX, phase, "poll"), {"state": "succeeded"})
            if phase == "classify":
                engine.remove_covers(FAX)
            elif phase == "type":
                if engine.decide_type(FAX)["state"] == "terminal":
                    return store, starts
        engine.publish(FAX)
        return store, starts

    def test_all_types_actual_engine_steps_use_distinct_classifiers_and_body_only(self):
        from io import BytesIO
        from pypdf import PdfReader
        for kind in ("exam", "order", "rx"):
            with self.subTest(kind=kind):
                store, starts = self.run_pipeline(kind)
                self.assertEqual(store.job(FAX)["status"], "Completed")
                self.assertEqual([entry[1] for entry in starts], ["mock-pages", "mock-types", "mock-" + kind])
                self.assertEqual([entry[2] for entry in starts], ["perPage", "auto", "perPage"])
                self.assertEqual([len(PdfReader(BytesIO(entry[3])).pages) for entry in starts], [2, 1, 1])
                self.assertEqual(store.json("extracted", FAX + "/canonical.json")["documentType"], kind)
                report = store.json("extracted", FAX + "/report.json")
                self.assertEqual(report["sourceLabel"], "clinic-a")
                self.assertEqual(report["configSha256"], hashlib.sha256(encode(config())).hexdigest())
                self.assertEqual(report["modelIds"], {"pageClassifier": "mock-pages",
                                                     "faxTypeClassifier": "mock-types", "extractor": "mock-" + kind})
                self.assertEqual(report["documentIntelligenceApiVersion"], "2024-11-30")
                self.assertEqual(report["hl7Disposition"], "Validated")
                self.assertEqual(report["hl7Version"], "2.5.1")
                self.assertEqual(report["publishedRouteCount"],
                                 len(store.json("extracted", FAX + "/manifest.json")["routes"]))
                for phase in ("prepare", "classify", "removeCovers", "type", "extract"):
                    self.assertIn("startedAt", report["phaseTimings"][phase])
                    self.assertIn("completedAt", report["phaseTimings"][phase])
                self.assertIn("decisionAt", report["phaseTimings"]["publish"])
                self.assertNotIn("sensitive", json.dumps(report))

    def test_unknown_and_low_type_never_extract_or_publish(self):
        for label, score in (("unknown", .99), ("exam", .7999)):
            with self.subTest(label=label):
                store, starts = self.run_pipeline(label=label, score=score)
                self.assertEqual(store.job(FAX)["status"], "PendingHumanReview")
                self.assertEqual(len(starts), 2)
                self.assertFalse(any(c in {"extracted", "routed"} for c, _ in store.objects))
                evidence = store.json("human-review", FAX + "/review.json")
                self.assertEqual(evidence["classifications"]["type"]["documents"][0]["confidence"], score)

    def test_auto_split_mixed_logical_results_are_preserved_for_review(self):
        store = MemoryStore()
        original = pdf(3)
        from pipeline.policy import fingerprint
        store.intake(FAX, original, {"source": "clinic-a", "mime": "application/pdf", "owner": OWNER,
                                    "fingerprint": fingerprint(original, "clinic-a", "application/pdf", OWNER)}, config())
        engine = Engine(store, FakeAI())
        engine.prepare(FAX)
        store.immutable_json("working", FAX + "/classify-result.json", {"documents": [
            {"docType": "cover" if page == 1 else "body", "confidence": .99,
             "boundingRegions": [{"pageNumber": page}]} for page in range(1, 4)]})
        engine.remove_covers(FAX)
        mixed = {"documents": [
            {"docType": "exam", "confidence": .99, "boundingRegions": [{"pageNumber": 1}]},
            {"docType": "order", "confidence": .99, "boundingRegions": [{"pageNumber": 2}]}]}
        store.immutable_json("working", FAX + "/type-result.json", mixed)
        self.assertEqual(engine.decide_type(FAX), {"state": "terminal"})
        self.assertEqual(store.job(FAX)["reason"], "mixed_or_ambiguous_fax_type")
        self.assertEqual(store.json("human-review", FAX + "/review.json")["classifications"]["type"], mixed)
        self.assertFalse(any(c in {"extracted", "routed"} for c, _ in store.objects))

    def test_auto_missing_mapping_holds_and_explicit_not_applicable_completes(self):
        store, _ = self.run_pipeline(mapping="missing")
        self.assertEqual(store.job(FAX)["reason"], "missing_hl7_mapping")
        self.assertFalse(any(c in {"extracted", "routed"} for c, _ in store.objects))
        store, _ = self.run_pipeline(mapping="not-applicable")
        self.assertEqual(store.job(FAX)["status"], "Completed")
        self.assertFalse(any(name.endswith(".hl7") for _, name in store.objects))
        self.assertEqual(store.json("extracted", FAX + "/report.json")["hl7Disposition"], "NotApplicable")

    def test_rest_classifier_split_and_preflight_metadata_contract(self):
        ai = Intelligence("https://example.cognitiveservices.azure.com", None)
        op = ai.endpoint + "/documentintelligence/documentClassifiers/mock-types/analyzeResults/abc?api-version=2024-11-30"
        with patch.object(ai, "request", return_value=(202, {"operation-location": op}, {})) as request:
            ai.start("documentClassifiers", "mock-types", b"synthetic", split="auto")
            self.assertIn("&split=auto", request.call_args.args[1])
        c = config()
        responses = [
            (200, {}, {"apiVersion": "2024-11-30", "docTypes": {"cover": {}, "body": {}}}),
            (200, {}, {"apiVersion": "2024-11-30", "docTypes": {"exam": {}, "order": {}, "rx": {}}}),
        ]
        for kind in ("exam", "order", "rx"):
            responses.append((200, {}, {"docTypes": {kind: {"fieldSchema": {
                field: {"type": "string"} for field in c["documentTypes"][kind]["expectedFields"]}}}}))
        with patch.object(ai, "request", side_effect=responses) as request:
            ai.preflight(c)
            self.assertEqual(request.call_count, 5)
        wrong_type = copy.deepcopy(responses)
        wrong_type[2][2]["docTypes"]["exam"]["fieldSchema"]["PatientName"]["type"] = "boolean"
        with patch.object(ai, "request", side_effect=wrong_type), self.assertRaisesRegex(
                Rejected, "^model_schema_type_mismatch$"):
            ai.preflight(c)
        responses[1] = (200, {}, {"apiVersion": "2024-11-30", "docTypes": {"unknown": {}}})
        with patch.object(ai, "request", side_effect=responses), self.assertRaises(Rejected):
            ai.preflight(c)


class ReportingTests(unittest.TestCase):
    def test_report_emits_only_safe_metric_contract_after_immutable_write(self):
        store = MemoryStore()
        job = {
            "faxId": FAX,
            "status": "PendingHumanReview",
            "createdAt": "2026-09-15T12:00:00+00:00",
            "updatedAt": "2026-09-15T12:01:00+00:00",
            "reviewEnteredAt": "2026-09-15T12:01:00+00:00",
            "config": config(),
            "source": "clinic-a",
            "documentType": "exam",
            "values": {"PatientName": "NEVER_EXPORT"},
            "reviewReason": "NEVER_EXPORT",
        }
        with patch("pipeline.engine.logging.info") as info:
            Engine(store, None)._report(job, FAX + "/report.json")
        self.assertTrue(store.exists("human-review", FAX + "/report.json"))
        self.assertEqual(info.call_args.args[0], "FAX_METRIC %s")
        payload = json.loads(info.call_args.args[1])
        self.assertEqual(payload, store.json("human-review", FAX + "/report.json"))
        self.assertNotIn("NEVER_EXPORT", info.call_args.args[1])

    def test_reports_exclude_unconfigured_source_raw_values_and_unbounded_identifiers(self):
        job = {
            "faxId": FAX, "status": "PendingHumanReview", "createdAt": "2026-09-15T12:00:00+00:00",
            "updatedAt": "2026-09-15T12:00:00+00:00", "config": config(),
            "source": "RAW_SENDER_NOT_CONFIGURED", "documentType": "exam",
            "values": {"PatientName": "NEVER_EXPORT"}, "reviewReason": "NEVER_EXPORT",
            "modelIds": {"extractor": "https://unsafe.example/NEVER_EXPORT"},
            "hl7Disposition": {"status": "NotApplicable", "reason": "NEVER_EXPORT"},
            "phaseTimings": {"extract": {"startedAt": "NEVER_EXPORT", "raw": "NEVER_EXPORT"},
                             "NEVER_EXPORT": {"startedAt": "2026-09-15T12:00:00+00:00"}},
        }
        result = facts(job)
        self.assertNotIn("sourceLabel", result)
        self.assertNotIn("modelIds", result)
        self.assertNotIn("phaseTimings", result)
        self.assertNotIn("NEVER_EXPORT", json.dumps(result))
        self.assertNotIn("RAW_SENDER", json.dumps(result))
        self.assertEqual(result["hl7Disposition"], "NotApplicable")
        self.assertIn("not-measured", result["qualityTruth"])

    def test_counts_preserve_zero_and_unknowns_are_not_fabricated(self):
        job = {"faxId": FAX, "status": "Completed", "createdAt": "2026-09-15T12:00:00+00:00",
               "updatedAt": "2026-09-15T12:00:00+00:00", "publication": {"manifestReady": True}}
        result = facts(job)
        for key in ("sourceLabel", "configSha256", "modelIds", "hl7Disposition",
                    "phaseTimings", "matchedRouteCount", "publishedRouteCount"):
            self.assertNotIn(key, result)
        job["matchedRouteCount"] = 0
        result = facts(job)
        self.assertEqual(result["matchedRouteCount"], 0)
        self.assertEqual(result["publishedRouteCount"], 0)
        job["status"] = "Publishing"
        self.assertNotIn("publishedRouteCount", facts(job))
        job["matchedRouteCount"] = False
        self.assertNotIn("matchedRouteCount", facts(job))
        job["status"] = "Completed"
        job["completedAt"] = "2030-01-01T00:00:00+00:00"
        self.assertNotIn("completedAt", facts(job))
        job["completedAtSource"] = "manifestLastModified"
        self.assertEqual(facts(job)["completedAt"], job["completedAt"])


if __name__ == "__main__":
    unittest.main()
