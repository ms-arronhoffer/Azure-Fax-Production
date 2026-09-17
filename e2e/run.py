"""Held-out real-inference runner. Cloud submissions require explicit --execute."""
import argparse
import io
import json
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from pypdf import PdfReader

from fixtures import digest, self_check, write_json
from train import safe_url


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def normal(value):
    return " ".join(str(value).strip().split()).casefold()


def normalized_field(name, value):
    if name == "Dose" and type(value) in (str, int, float):
        from decimal import Decimal, InvalidOperation
        try:
            number = Decimal(str(value))
            if number.is_finite() and number >= 0:
                return str(number.normalize())
        except InvalidOperation:
            pass
    return normal(value)


def hl7_assertions(text, truth, kind):
    segments = {}
    for line in text.rstrip("\r").split("\r"):
        parts = line.split("|")
        segments.setdefault(parts[0], []).append(parts)
    checks = []

    def field(segment, index, expected, component=None):
        rows = segments.get(segment, [])
        actual = rows[0][index] if rows and len(rows[0]) > index else ""
        if component is not None:
            parts = actual.split("^")
            actual = parts[component] if len(parts) > component else ""
        value_name = "Dose" if segment == "RXE" and index == 3 else ""
        checks.append({"name": f"hl7-field:{segment}.{index}", "passed":
                       normalized_field(value_name, actual) == normalized_field(value_name, expected)})

    field("PID", 3, truth["PatientId"])
    field("PID", 5, truth["PatientName"])
    field("PID", 7, truth["DateOfBirth"].replace("-", ""))
    field("PV1", 19, truth["EncounterId"])
    if kind in {"exam", "order"}:
        field("OBR", 7, truth["DocumentDate"].replace("-", ""))
        field("OBR", 16, truth["ProviderId"])
        if kind == "exam":
            field("OBR", 4, truth["ExamName"], 1)
            field("OBX", 5, truth["ResultText"])
        else:
            field("OBR", 4, truth["OrderCode"], 0)
            field("OBR", 4, truth["OrderDescription"], 1)
    else:
        field("ORC", 9, truth["DocumentDate"].replace("-", ""))
        field("ORC", 12, truth["ProviderId"])
        field("RXE", 2, truth["MedicationCode"], 0)
        field("RXE", 2, truth["MedicationName"], 1)
        field("RXE", 3, truth["Dose"])
        field("RXR", 1, truth["Route"])
        field("TQ1", 3, truth["Frequency"])
    return checks


def marker(fax, case, source):
    return {"schemaVersion": 1, "faxId": fax, "source": source, "mime": case["mime"],
            "original": {"container": "incoming", "name": f"{fax}/original"}, "sha256": case["sha256"]}


def report(path, results):
    write_json(path / "evidence.json", {"schemaVersion": 1, "testOnly": True,
                                       "suite": "real-inference", "cases": results})
    suite = ET.Element("testsuite", name="synthetic-fax-real-inference", tests=str(len(results)),
                       failures=str(sum(r["status"] == "failed" for r in results)),
                       skipped=str(sum(r["status"] in {"skipped", "inconclusive", "not_run"} for r in results)))
    for result in results:
        case = ET.SubElement(suite, "testcase", name=result["id"], time=str(result.get("elapsedSeconds", 0)))
        if result["status"] == "failed":
            ET.SubElement(case, "failure", message="Assertions failed").text = json.dumps(result["assertions"])
        elif result["status"] != "passed":
            ET.SubElement(case, "skipped", message=result.get("reason", result["status"]))
        ET.SubElement(case, "system-out").text = json.dumps(result, sort_keys=True)
    ET.ElementTree(suite).write(path / "junit.xml", encoding="utf-8", xml_declaration=True)


def observer_client_id(config):
    observer = config.get("observerClientId") or config.get("readerClientId")
    if not observer:
        raise ValueError("Explicit observerClientId required; uploader cannot read output artifacts")
    identity = uuid.UUID(observer)
    if config.get("readerClientId") and uuid.UUID(config["readerClientId"]) != identity:
        raise ValueError("observerClientId and legacy readerClientId must identify the same identity")
    if identity == uuid.UUID(config["runnerClientId"]):
        raise ValueError("Observer identity must differ from uploader identity")
    return str(identity)


class Runner:
    def __init__(self, config, root, evidence, cap, timeout):
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import BlobServiceClient
        import requests
        self.config, self.root, self.evidence = config, root, evidence
        self.timeout, self.cap = timeout, cap
        self.credential = ManagedIdentityCredential(client_id=config["runnerClientId"])
        self.blobs = BlobServiceClient(config["storageUrl"], credential=self.credential, retry_total=3,
                                       connection_timeout=15, read_timeout=60, logging_enable=False)
        reader = ManagedIdentityCredential(client_id=observer_client_id(config))
        self.read_blobs = BlobServiceClient(config["storageUrl"], credential=reader, retry_total=3,
                                            connection_timeout=15, read_timeout=60, logging_enable=False)
        self.http = requests.Session()
        self.state_path = evidence / "run-state.json"
        fingerprint = digest(canonical_bytes(config) + (root / "manifest.json").read_bytes())
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            "fingerprint": fingerprint, "attempts": 0, "cases": {}, "protocol": {}}
        if self.state["fingerprint"] != fingerprint:
            raise ValueError("Run-state config or corpus mismatch")

    def save(self):
        staged = self.state_path.with_suffix(".pending")
        write_json(staged, self.state)
        staged.replace(self.state_path)

    def attempt(self):
        if self.state["attempts"] >= self.cap:
            raise RuntimeError("Persistent test attempt safety cap reached")
        self.state["attempts"] += 1
        self.save()

    def read(self, container, name):
        return self.read_blobs.get_blob_client(container, name).download_blob(logging_enable=False).readall()

    def get_json(self, container, name):
        return json.loads(self.read(container, name))

    def put(self, container, name, data, mime, source):
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings
        blob = self.blobs.get_blob_client(container, name)
        try:
            blob.upload_blob(data, overwrite=False, content_settings=ContentSettings(content_type=mime),
                             metadata={"sha256": digest(data), "source": source, "mime": mime, "testonly": "true"},
                             logging_enable=False)
        except ResourceExistsError:
            if digest(self.read(container, name)) != digest(data):
                raise RuntimeError("Immutable direct-upload conflict") from None

    def upload(self, case, fax, source):
        data = (self.root / case["path"]).read_bytes()
        if digest(data) != case["sha256"]:
            raise ValueError("Fixture digest changed")
        self.put("incoming", f"{fax}/original", data, case["mime"], source)
        self.put("ready", f"{fax}.json", canonical_bytes(marker(fax, case, source)), "application/json", source)

    def wait(self, fax):
        from azure.core.exceptions import ResourceNotFoundError
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            try:
                job = self.get_json("jobs", fax + ".json")
                if job["status"] in {"Completed", "PendingHumanReview", "Failed"}:
                    return job
            except ResourceNotFoundError:
                pass
            time.sleep(5)
        return None

    def http_review(self, method, fax, body=None, etag=None, denied=False, original=False):
        credential = self.credential
        client_id = self.config.get("deniedClientId" if denied else "reviewerClientId")
        if client_id:
            from azure.identity import ManagedIdentityCredential
            credential = ManagedIdentityCredential(client_id=client_id)
        token = credential.get_token(self.config["apiAudience"].rstrip("/") + "/.default").token
        headers = {"Authorization": "Bearer " + token}
        if etag:
            headers["If-Match"] = etag
        route = f"/api/faxes/{fax}/review" + ("/original" if original else "")
        return self.http.request(method, self.config["apiUrl"].rstrip("/") + route,
                                 headers=headers, json=body, timeout=(15, 120), allow_redirects=False)

    def inspect(self, case, job, result, corrected=False):
        assertions = result["assertions"]
        check = lambda name, ok: assertions.append({"name": name, "passed": bool(ok)})
        expected = case["expected"]
        desired = "Completed" if corrected else {"complete": "Completed", "needs_review": "PendingHumanReview"}[expected["state"]]
        check("terminal-state", job["status"] == desired)
        if expected.get("reason") and not corrected:
            check("expected-reason", job.get("reason") == expected["reason"])
        if expected.get("hl7") == "error" and not corrected:
            check("real-hl7-validation-failure", job.get("reason") == "hl7_profile_validation_failed")
        result["actual"] = {k: job.get(k) for k in [
            "status", "reason", "documentType", "bodyPageCount", "coverPageCount",
            "completionMode", "fieldMinimumConfidence", "faxTypeConfidence"]}
        result["actual"]["configSha256"] = digest(canonical_bytes(job["config"]))
        check("pinned-runtime-config", result["actual"]["configSha256"] == self.config["runtimeConfigSha256"])
        check("original-hash", digest(self.read("incoming", f"{job['faxId']}/original")) == case["sha256"])
        if job["status"] != "Completed":
            review = self.get_json("human-review", f"{job['faxId']}/review.json")
            result["actual"]["reviewEvidenceSha256"] = digest(canonical_bytes(review))
            check("review-original-preserved", digest(self.read("human-review", f"{job['faxId']}/original")) == case["sha256"])
            check("no-automatic-manifest", not self.read_blobs.get_blob_client("extracted", f"{job['faxId']}/manifest.json").exists())
            return
        check("body-page-count", job.get("bodyPageCount") == case["bodyPages"])
        check("document-type", job.get("documentType") == expected["documentType"])
        check("completion-mode", job.get("completionMode") == ("Reviewed" if corrected else "Automatic"))
        manifest = self.get_json(job["manifest"]["container"], job["manifest"]["name"])
        result["actual"]["manifest"] = manifest
        result["actual"]["manifestSha256"] = digest(canonical_bytes(manifest))
        check("manifest-commit", manifest["status"] == "Completed")
        check("route-count", len(manifest["routes"]) == expected["routeCount"])
        check("manifest-config", manifest["configSha256"] == self.config["runtimeConfigSha256"])
        report_name = job["manifest"]["name"].rsplit("/", 1)[0] + "/report.json"
        check("report-hash-bound-in-manifest", any(
            artifact["container"] == "extracted" and artifact["name"] == report_name
            for artifact in manifest["artifacts"]))
        hl7 = None
        actual_fields = None
        for artifact in manifest["artifacts"]:
            check("artifact-container", artifact["container"] in {"extracted", "routed"})
            if artifact["container"] not in {"extracted", "routed"}:
                continue
            raw = self.read(artifact["container"], artifact["name"])
            check("artifact-hash:" + artifact["name"], digest(raw) == artifact["sha256"])
            check("artifact-size:" + artifact["name"], len(raw) == artifact["bytes"])
            if artifact["name"].endswith("canonical.json"):
                canonical = json.loads(raw)
                actual_fields = canonical["values"]
                page_map = canonical["originalPageMap"]
                result["actual"]["pageMap"] = page_map
                check("cover-original-page-map", page_map["coverPages"] == case["coverPages"])
                check("body-original-page-map", page_map["bodyPages"] == [
                    p for p in range(1, case["pages"] + 1) if p not in case["coverPages"]])
                check("canonical-original-hash", canonical["provenance"]["originalSha256"] == case["sha256"])
            if artifact["container"] == "extracted" and artifact["name"].endswith("content.hl7"):
                hl7 = raw.decode("utf-8")
                result["actual"]["hl7Sha256"] = digest(raw)
            if artifact["container"] == "extracted" and artifact["name"] == report_name:
                operational = json.loads(raw)
                result["actual"]["operationalReport"] = operational
                check("report-completed", operational["status"] == "Completed")
                check("report-completion-mode", operational["completionMode"] == job["completionMode"])
                check("report-source-label", operational.get("sourceLabel") == self.config["sources"][case["sourceProfile"]])
                check("report-config-hash", operational.get("configSha256") == self.config["runtimeConfigSha256"])
                check("report-di-api-version", operational.get("documentIntelligenceApiVersion") == "2024-11-30")
                expected_models = {"pageClassifier": self.config["versions"]["pageClassifier"],
                                   "faxTypeClassifier": self.config["versions"]["typeClassifier"],
                                   "extractor": self.config["versions"][expected["documentType"] + "Model"]}
                check("report-configured-models", operational.get("configuredModelIds") == expected_models)
                if not corrected:
                    check("report-actual-models", operational.get("modelIds") == expected_models)
                check("report-published-route-count", operational.get("publishedRouteCount") == expected["routeCount"])
                check("report-hl7-disposition", operational.get("hl7Disposition") == (
                    "NotApplicable" if expected["hl7"] == "not_applicable" else "Validated"))
        check("canonical-exists", actual_fields is not None)
        truth = case["reviewCorrection"] if corrected else expected["fields"]
        result["actual"]["fields"] = actual_fields
        for name, value in truth.items():
            check("field:" + name, actual_fields is not None and
                  normalized_field(name, actual_fields.get(name, "")) == normalized_field(name, value))
        if expected["hl7"] == "not_applicable":
            check("hl7-not-applicable", hl7 is None and manifest["hl7Disposition"]["status"] == "NotApplicable")
        else:
            check("hl7-present", hl7 is not None)
            if hl7:
                msh = hl7.split("\r")[0].split("|")
                check("hl7-version-test", len(msh) > 11 and msh[10:12] == ["T", "2.5.1"])
                check("hl7-disposition", manifest["hl7Disposition"]["status"] == "Validated")
                check("hl7-patient", truth["PatientId"] in hl7)
                check("hl7-type", {"exam": "ORU^R01", "order": "ORM^O01", "rx": "RDE^O11"}[expected["documentType"]] in msh[8])
                assertions.extend(hl7_assertions(hl7, truth, expected["documentType"]))
        body_name = (job["manifest"]["name"].rsplit("/", 1)[0] + "/body.pdf") if corrected else f"{job['faxId']}/body.pdf"
        body = self.read("extracted" if corrected else "working", body_name)
        result["actual"]["bodySha256"] = digest(body)
        check("body-pdf-pages", len(PdfReader(io.BytesIO(body)).pages) == case["bodyPages"])

    def run_case(self, case):
        started = time.monotonic()
        result = {"id": case["id"], "status": "not_run", "fixtureSha256": case["sha256"],
                  "versions": self.config["versions"], "expected": case["expected"], "assertions": []}
        try:
            self.attempt()
            if case["id"] not in self.state["cases"]:
                self.state["cases"][case["id"]] = {"faxId": str(uuid.uuid4()), "status": "submitting"}
                self.save()
            record = self.state["cases"][case["id"]]
            fax = record["faxId"]
            result["faxId"] = fax
            source = self.config["sources"][case["sourceProfile"]]
            self.upload(case, fax, source)
            job = self.wait(fax)
            if job is None:
                result.update(status="inconclusive", reason="No terminal evidence before deadline; absence is not rejection proof")
            else:
                self.inspect(case, job, result)
                result["status"] = "passed" if all(a["passed"] for a in result["assertions"]) else "failed"
            record["status"] = result["status"]
            self.save()
        except Exception as error:
            result.update(status="failed", reason=type(error).__name__)
            result["assertions"].append({"name": "runner-completed", "passed": False, "errorType": type(error).__name__})
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        return result

    def protocol(self, name, case):
        result = {"id": name, "fixtureSha256": case["sha256"], "versions": self.config["versions"],
                  "expected": {"protocol": name}, "assertions": [], "status": "not_run"}
        started = time.monotonic()
        check = lambda n, ok: result["assertions"].append({"name": n, "passed": bool(ok)})
        attempt_started = False
        try:
            if name in self.state["protocol"]:
                result.update(status="skipped", reason="Protocol attempt already recorded; inspect existing evidence, do not replay")
                return result
            isolated_review = name in {"human-corrected-release", "concurrent-review"}
            if isolated_review or name == "admission-rejection":
                fax = str(uuid.uuid4())
            else:
                record = self.state["cases"].get(case["id"])
                if not record:
                    result.update(status="skipped", reason="Submit prerequisite held-out case first")
                    return result
                fax = record["faxId"]
            result["faxId"] = fax
            self.attempt()
            self.state["protocol"][name] = {"status": "started", "faxId": fax}
            attempt_started = True
            self.save()
            if isolated_review:
                self.upload(case, fax, self.config["sources"][case["sourceProfile"]])
                pending = self.wait(fax)
                if pending is None:
                    result.update(status="inconclusive", reason="Isolated review prerequisite has no terminal evidence")
                    return result
                prerequisite = {"expected": case["expected"], "assertions": []}
                self.inspect(case, pending, prerequisite)
                result["prerequisite"] = prerequisite
                ready = pending["status"] == "PendingHumanReview" and all(
                    assertion["passed"] for assertion in prerequisite["assertions"])
                check("isolated-review-prerequisite", ready)
                if not ready:
                    result.update(status="failed", reason="Isolated prerequisite did not match expected review outcome")
                    return result
            if name == "admission-rejection":
                from azure.core.exceptions import ResourceNotFoundError
                source = self.config["sources"][case["sourceProfile"]]
                self.put("incoming", f"{fax}/original", (self.root / case["path"]).read_bytes(), case["mime"], source)
                invalid = {**marker(fax, case, source), "sha256": "0" * 64}
                raw = canonical_bytes(invalid)
                marker_hash = digest(raw)
                self.put("ready", fax + ".json", raw, "application/json", source)
                receipt = None
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    try:
                        receipt = self.get_json("jobs", f"_admission-rejections/{fax}/{marker_hash}.json")
                        break
                    except ResourceNotFoundError:
                        time.sleep(5)
                if receipt is None:
                    result.update(status="inconclusive", reason="No positive admission rejection receipt; absence is not proof")
                    return result
                result["actual"] = receipt
                result["expected"] = {"status": "RejectedAttempt", "reason": "original_digest_mismatch",
                                      "manifestSha256": marker_hash}
                check("rejected-attempt-status", receipt["status"] == "RejectedAttempt")
                check("rejection-reason", receipt["reason"] == "original_digest_mismatch")
                check("exact-manifest-digest", receipt["manifestSha256"] == marker_hash)
                check("entire-manifest-hashed", receipt["digestScope"] == "entire-manifest" and receipt["hashedBytes"] == len(raw))
                check("rejection-config-pinned", receipt["configSha256"] == self.config["runtimeConfigSha256"])
                check("rejected-original-retained", digest(self.read("incoming", f"{fax}/original")) == case["sha256"])
                result["scope"] = "One positively observed rejected admission attempt; not terminal job fate or later retry outcome"
            elif name in {"duplicate", "conflict"}:
                source = self.config["sources"][case["sourceProfile"]]
                before = self.read("ready", fax + ".json")
                if name == "duplicate":
                    self.upload(case, fax, source)
                    check("identical-marker-unchanged", self.read("ready", fax + ".json") == before)
                    # Rewriting isn't needed: duplicate-trigger handling is tested deterministically in app/tests.
                    result["scope"] = "immutable upload replay; not a forced second trigger"
                else:
                    from azure.core.exceptions import ResourceExistsError
                    try:
                        self.blobs.get_blob_client("ready", fax + ".json").upload_blob(b"TEST ONLY CONFLICT", overwrite=False)
                        check("conflicting-create-denied", False)
                    except ResourceExistsError:
                        check("conflicting-create-denied", True)
                    check("original-marker-retained", self.read("ready", fax + ".json") == before)
                    result["scope"] = "storage conditional-create conflict; not adversarial overwrite admission"
            elif name == "denied-review":
                if not self.config.get("deniedClientId"):
                    result.update(status="skipped", reason="Separate MI without Fax.Review must be assigned to runner")
                    return result
                response = self.http_review("GET", fax, denied=True)
                check("non-reviewer-denied", response.status_code == 403)
                response = self.http_review("POST", fax, body={"reason": "TEST ONLY denied-role probe"}, denied=True)
                check("non-reviewer-write-denied", response.status_code == 403)
                response = self.http_review("GET", fax, denied=True, original=True)
                check("non-reviewer-original-denied", response.status_code == 403)
            else:
                response = self.http_review("GET", fax)
                if response.status_code != 200 or response.json().get("status") not in {"PendingHumanReview", "Failed"}:
                    result.update(status="skipped", reason="Requires real inference pending review and reviewer role")
                    return result
                etag = response.headers.get("ETag")
                original = self.http_review("GET", fax, original=True)
                original_ok = (original.status_code == 200 and digest(original.content) == case["sha256"]
                               and original.headers.get("Content-Type", "").split(";")[0] == case["mime"])
                check("review-api-original-hash", original_ok)
                check("review-api-original-mime", original.headers.get("Content-Type", "").split(";")[0] == case["mime"])
                if not original_ok:
                    result.update(status="failed", reason="Reviewer could not retrieve the exact original; no release attempted")
                    return result
                body = {"documentType": case["expected"]["documentType"], "bodyPages": [1],
                        "values": case["reviewCorrection"], "reason": "TEST ONLY synthetic ground-truth correction"}
                if name == "concurrent-review":
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        responses = list(pool.map(lambda _: self.http_review("POST", fax, body, etag), range(2)))
                    statuses = sorted(r.status_code for r in responses)
                    result["actualHttpStatuses"] = statuses
                    # Lease contention can be a retryable 503; stale ETag must still be 409 after commit.
                    check("one-release-only", statuses.count(200) == 1 and statuses[1] in {409, 503})
                    check("stale-review-rejected", self.http_review("POST", fax, body, etag).status_code == 409)
                else:
                    response = self.http_review("POST", fax, body, etag)
                    check("review-released", response.status_code == 200)
                job = self.wait(fax)
                if job:
                    self.inspect(case, job, result, corrected=True)
                else:
                    check("review-completion-evidence", False)
            result["status"] = "passed" if result["assertions"] and all(a["passed"] for a in result["assertions"]) else "failed"
        except Exception as error:
            result.update(status="failed", reason=type(error).__name__)
            check("protocol-completed", False)
        finally:
            result["elapsedSeconds"] = round(time.monotonic() - started, 3)
            if attempt_started:
                self.state["protocol"][name]["result"] = result
                self.save()
        return result


def validate(config):
    safe_url(config["storageUrl"], ".blob.core.windows.net")
    safe_url(config["apiUrl"], ".azurewebsites.net")
    for key in ["storageUrl", "apiUrl"]:
        if urlparse(config[key]).path not in {"", "/"}:
            raise ValueError("Service root URL required")
    uuid.UUID(config["runnerClientId"])
    if config.get("deniedClientId"):
        uuid.UUID(config["deniedClientId"])
    if config.get("reviewerClientId"):
        uuid.UUID(config["reviewerClientId"])
    observer_client_id(config)
    if not config["apiAudience"].startswith("api://"):
        raise ValueError("Explicit API application audience required")
    uuid.UUID(config["apiAudience"][6:])
    import re
    if not re.fullmatch("[0-9a-f]{64}", config["runtimeConfigSha256"]):
        raise ValueError("SHA256 of actual canonical runtime config required")
    required = {"pageClassifier", "typeClassifier", "examModel", "orderModel", "rxModel", "deployment", "calibration"}
    if not required <= config["versions"].keys() or any(not config["versions"][k] for k in required):
        raise ValueError("Pin deployed model/build/calibration versions")
    for key in ["one", "none", "multi", "hl7-not_applicable", "hl7-error"]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", config["sources"][key]):
            raise ValueError("Explicit configured synthetic source profiles required")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument("--protocol", choices=["duplicate", "conflict", "admission-rejection", "human-corrected-release",
                                             "denied-review", "concurrent-review"])
    parser.add_argument("--max-attempts", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=900)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_attempts <= 30 or not 5 <= args.timeout <= 1800:
        parser.error("max-attempts must be 1..30 and timeout 5..1800 seconds")
    config = json.loads(args.config.read_text())
    validate(config)
    self_check(args.root)
    manifest = json.loads((args.root / "manifest.json").read_text())
    case_names = args.case or config.get("selectedCases")
    selected = [case for case in manifest["cases"] if not case_names or case["id"] in case_names]
    if case_names and set(case_names) - {c["id"] for c in selected}:
        parser.error("Unknown case ID")
    if args.protocol:
        selected = [c for c in manifest["cases"] if c["id"] == (
            "exam-pdf-body" if args.protocol in {"duplicate", "conflict", "admission-rejection"} else "missing-required")]
    if not args.execute:
        print(json.dumps({"status": "not_run", "network": "not_contacted", "cases": [c["id"] for c in selected],
                          "protocol": args.protocol, "maxAttempts": args.max_attempts,
                          "contract": marker(str(uuid.UUID(int=1)), selected[0], config["sources"][selected[0]["sourceProfile"]])}, indent=2))
    else:
        runner = Runner(config, args.root, args.evidence, args.max_attempts, args.timeout)
        results = []
        for case in selected:
            result = runner.protocol(args.protocol, case) if args.protocol else runner.run_case(case)
            results.append(result)
            write_json(args.evidence / (result["id"] + ".json"), result)
            report(args.evidence, results)
        raise SystemExit(1 if any(r["status"] != "passed" for r in results) else 0)
