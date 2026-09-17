"""Local contract tests only: not cloud inference evidence."""
import json
import unittest
from pathlib import Path

from fixtures import COMMON, SPECIFIC, values
from run import Runner, hl7_assertions, marker, normal, report
from train import Journal, ocr_labels, plan, promoted_settings, safe_url, validate_config


class FixtureContracts(unittest.TestCase):
    def test_exact_copied_model_type_prefix(self):
        from configure_runtime import APP
        from pipeline.contracts import result_type_matches
        self.assertTrue(result_type_matches("form", "form", "model"))
        self.assertTrue(result_type_matches("model:form", "form", "model"))
        self.assertFalse(result_type_matches("other:form", "form", "model"))
        self.assertFalse(result_type_matches("None:form", "form", None))

    def test_private_runner_hash_matches_runtime_canonical_json(self):
        import sys
        import tempfile
        from run import canonical_bytes
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from private_runner import config_hash
        from fixtures import digest
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            value = {"source": "clinic-a", "nested": {"z": True, "a": 0}}
            target.write_text(json.dumps(value, indent=2))
            self.assertEqual(config_hash(target), digest(canonical_bytes(value)))
            target.write_text(json.dumps(value, sort_keys=True))
            self.assertEqual(config_hash(target), digest(canonical_bytes(value)))

    def test_private_runner_qualifies_before_enabling_local_configuration(self):
        import sys
        import tempfile
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import private_runner
        identity = {"clientId": "unit-client", "principalId": "unit-principal"}
        values = {"uploaderIdentity": identity, "authorIdentity": identity, "reviewerIdentity": identity,
                  "dataStorageAccountUrl": "https://unit.blob.core.windows.net/",
                  "functionAppName": "unit-fax", "apiClientId": "unit-api"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            with patch.object(private_runner, "ROOT", root), patch.object(private_runner, "ARTIFACTS", root / "artifacts"), \
                    patch.object(private_runner, "check_network"), \
                    patch.object(private_runner, "corpus_settings", return_value=({}, root / "artifacts/corpus", "")), \
                    patch.object(private_runner.subprocess, "run") as run:
                private_runner.qualify(values, root / "artifacts", "baseline")
                first, second = [call.args[0] for call in run.call_args_list]
                self.assertIn("e2e/evaluate_development.py", first)
                self.assertIn("--execute", first)
                self.assertIn("e2e/configure_runtime.py", second)
                self.assertIn("--development-evidence", second)
                self.assertIn("--enable-config", second)
                self.assertEqual(second[second.index("--variant") + 1], "happy")

    def test_mutating_review_protocols_use_isolated_faxes_and_single_attempt(self):
        from types import SimpleNamespace
        runner = Runner.__new__(Runner)
        runner.config = {"versions": {}, "sources": {"one": "clinic-a"}}
        runner.state = {"cases": {"missing-required": {"faxId": "original-case"}},
                        "protocol": {}, "attempts": 0}
        runner.cap, runner.timeout = 30, 1
        runner.save = lambda: None
        uploaded = []
        runner.upload = lambda case, fax, source: uploaded.append(fax)
        runner.wait = lambda fax: {"status": "PendingHumanReview"}
        runner.inspect = lambda case, job, result: result["assertions"].append({"name": "seed", "passed": True})
        runner.http_review = lambda *args, **kwargs: SimpleNamespace(status_code=403)
        case = {"id": "missing-required", "sha256": "unit-only", "sourceProfile": "one",
                "expected": {"state": "needs_review"}}
        for name in ("human-corrected-release", "concurrent-review"):
            runner.protocol(name, case)
        self.assertEqual(runner.state["attempts"], 2)
        self.assertEqual(len(set(uploaded)), 2)
        self.assertNotIn("original-case", uploaded)
        original_result = runner.state["protocol"]["human-corrected-release"]["result"]
        runner.protocol("human-corrected-release", case)
        self.assertIs(runner.state["protocol"]["human-corrected-release"]["result"], original_result)
        self.assertEqual(runner.state["attempts"], 2)
        self.assertEqual(len(uploaded), 2)

    def test_development_uses_rest_split_parameter(self):
        import tempfile
        from unittest.mock import patch
        from evaluate_development import evaluate, model_ids
        from fixtures import digest, write_json
        promoted = {"runtimeResourceId": "unit-only", "runtimeEndpoint": "https://unit.cognitiveservices.azure.com",
                    "modelSettings": {"classifier": {"id": "unit-page"},
                                      "faxTypeClassifier": {"id": "unit-type"},
                                      "documentTypes": {kind: {"modelId": "unit-" + kind, "resultDocType": kind}
                                                        for kind in SPECIFIC}}}
        config = {"runnerClientId": "unit-only", "runtime": {
            "endpoint": promoted["runtimeEndpoint"], "models": model_ids(promoted)}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries, by_bytes = [], {}
            for kind in SPECIFIC:
                for number in (1, 2):
                    raw = f"{kind}-{number}".encode()
                    entry = {"path": f"development/{kind}-{number}.pdf", "sha256": digest(raw),
                             "kind": kind, "split": "development", "pages": 1,
                             "fields": values(kind, "development", number)}
                    path = root / entry["path"]
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(raw)
                    entries.append(entry)
                    by_bytes[raw] = entry
            write_json(root / "manifest.json", {"assets": entries})

            def operation(key, path, **kwargs):
                import base64
                entry = by_bytes[base64.b64decode(kwargs["json"]["base64Source"])]
                if key.endswith("-page") or key.endswith("-type"):
                    page_phase = key.endswith("-page")
                    self.assertEqual(kwargs["params"], {"_overload": "classifyDocument", "split": "perPage" if page_phase else "auto"})
                    result = {"documents": [{"docType": "body" if page_phase else entry["kind"],
                              "confidence": .99, "boundingRegions": [{"pageNumber": 1}]}]}
                else:
                    self.assertEqual(kwargs["params"], {"_overload": "analyzeDocument"})
                    result = {"documents": [{"docType": entry["kind"], "fields": {
                        name: {"valueString": value, "confidence": .99}
                        for name, value in entry["fields"].items()}}]}
                return {"analyzeResult": result}

            with patch("azure.identity.ManagedIdentityCredential"), patch("evaluate_development.Client") as client:
                client.return_value.operation.side_effect = operation
                result = evaluate(config, promoted, root, root / "state.json", root / "result.json")
                self.assertEqual(result["status"], "passed")
                self.assertEqual(client.return_value.operation.call_count, 18)

    def test_observer_identity_is_explicit_and_separate(self):
        from run import observer_client_id
        uploader = "11111111-1111-1111-1111-111111111111"
        observer = "22222222-2222-2222-2222-222222222222"
        self.assertEqual(observer_client_id({"runnerClientId": uploader, "observerClientId": observer}), observer)
        self.assertEqual(observer_client_id({"runnerClientId": uploader, "readerClientId": observer}), observer)
        for config in ({"runnerClientId": uploader},
                       {"runnerClientId": uploader, "observerClientId": uploader},
                       {"runnerClientId": uploader, "observerClientId": observer, "readerClientId": uploader}):
            with self.assertRaises(ValueError):
                observer_client_id(config)

    def test_fields_non_actionable(self):
        for kind in SPECIFIC:
            fields = values(kind, "heldout", 101)
            self.assertEqual(set(fields), set(COMMON + SPECIFIC[kind]))
            self.assertTrue(fields["PatientId"].startswith("TEST"))
        rx = values("rx", "heldout", 1)
        self.assertEqual(rx["Dose"], "0")
        self.assertEqual(rx["Frequency"], "NEVER ADMINISTER")

    def test_marker_exact_contract(self):
        case = {"mime": "application/pdf", "sha256": "a" * 64}
        fax = "00000000-0000-0000-0000-000000000001"
        result = marker(fax, case, "test-one")
        self.assertEqual(set(result), {"schemaVersion", "faxId", "source", "mime", "original", "sha256"})
        self.assertEqual(result["original"], {"container": "incoming", "name": fax + "/original"})

    def test_labels_use_actual_ocr_and_normalized_polygon(self):
        polygon = [1, 2, 3, 2, 3, 3, 1, 3]
        geometric = {"labels": [{"label": "PatientName", "value": [
            {"page": 1, "text": "TEST PERSON", "boundingBoxes": [[.1, .2, .5, .2, .5, .3, .1, .3]]}]}]}
        result = {"analyzeResult": {"pages": [{"pageNumber": 1, "width": 10, "height": 10, "words": [
            {"content": "TEST", "polygon": polygon},
            {"content": "PERSON", "polygon": [3, 2, 5, 2, 5, 3, 3, 3]}]}]}}
        labels = ocr_labels("a.pdf", {"PatientName": "TEST PERSON"}, geometric, result)
        self.assertEqual(len(labels["labels"][0]["value"]), 2)
        self.assertEqual(labels["labels"][0]["value"][0]["boundingBoxes"][0], [v / 10 for v in polygon])
        self.assertEqual(ocr_labels("a.pdf", {"PatientName": "TEST PERSON"}, labels, result), labels)
        with self.assertRaises(ValueError):
            ocr_labels("a.pdf", {"PatientName": "OTHER PERSON"}, geometric, result)

    def test_build_calls_are_2024_contracts_and_train_only(self):
        config = {"trainingContainerUrl": "https://training.blob.core.windows.net/corpus",
                  "trainingPrefix": "v1/", "buildMode": "template", "maxTrainingHours": .5,
                  "authoring": {"models": {k: "synthetic-" + k for k in ["page", "type", *SPECIFIC]}}}
        requests = plan(config)
        self.assertEqual(len(requests), 5)
        self.assertEqual(set(requests[0][2]["docTypes"]), {"cover", "body"})
        self.assertEqual(set(requests[1][2]["docTypes"]), {"exam", "order", "rx"})
        self.assertNotIn("heldout", json.dumps(requests))
        self.assertNotIn("development", json.dumps(requests))
        for _, _, body in requests:
            self.assertFalse(body["allowOverwrite"])

    def test_no_sas_or_cross_origin(self):
        for value in ["http://training.blob.core.windows.net/a",
                      "https://training.blob.core.windows.net/a?sig=secret",
                      "https://example.org/a", "https://user:secret@training.blob.core.windows.net/a"]:
            with self.assertRaises(ValueError):
                safe_url(value, ".blob.core.windows.net")

    def test_empty_training_template_fails_closed(self):
        template = json.loads((Path(__file__).parent / "training.example.json").read_text())
        with self.assertRaises(ValueError):
            validate_config(template)

    def test_promotion_export_remains_disabled(self):
        ids = {key: "test-" + key for key in ["page", "type", *SPECIFIC]}
        config = {"runtime": {"models": ids, "resourceId": "test-only", "endpoint": "test-only"}}
        state = {"runtimeModels": {"page": {}, "type": {}, **{
            kind: {"docTypes": {"learned-" + kind: {"fieldSchema": {
                name: {"type": "string"} for name in COMMON + SPECIFIC[kind]}}}} for kind in SPECIFIC}}}
        result = promoted_settings(config, state)["modelSettings"]
        self.assertFalse(result["enabled"])
        self.assertEqual(result["faxTypeClassifier"]["calibrationId"], "")
        self.assertEqual(result["documentTypes"]["exam"]["resultDocType"], "learned-exam")
        with self.assertRaises(ValueError):
            promoted_settings(config, {})

    def test_generated_runtime_variants_use_real_schema(self):
        from configure_runtime import synthesize
        from pipeline.policy import matching_routes, validate_config
        ids = {key: "test-" + key for key in ["page", "type", *SPECIFIC]}
        model_config = {"runtime": {"models": ids, "resourceId": "unit-only",
                                   "endpoint": "https://unit-only.cognitiveservices.azure.com"}}
        state = {"runtimeModels": {"page": {}, "type": {}, **{
            kind: {"docTypes": {"learned-" + kind: {"fieldSchema": {
                name: {"type": "string"} for name in COMMON + SPECIFIC[kind]}}}} for kind in SPECIFIC}}}
        promoted = promoted_settings(model_config, state)
        manifest = {"cases": [{"id": name, "sourceProfile": profile} for name, profile in [
            ("exam-pdf-body", "one"), ("routes-none", "none"), ("routes-multi", "multi"),
            ("hl7-not-applicable", "hl7-not_applicable"), ("hl7-error", "hl7-error")]]}
        for variant, count in [("happy", 1), ("no-route", 0), ("multi-route", 2),
                               ("hl7-not-applicable", 1), ("hl7-error", 1)]:
            config, runner = synthesize(promoted, manifest,
                tenant_id="11111111-1111-1111-1111-111111111111",
                uploader_client_id="22222222-2222-2222-2222-222222222222",
                uploader_principal_id="33333333-3333-3333-3333-333333333333",
                reader_client_id="44444444-4444-4444-4444-444444444444",
                reviewer_client_id="55555555-5555-5555-5555-555555555555",
                storage_url="https://unittest.blob.core.windows.net",
                api_url="https://unittest.azurewebsites.net",
                api_audience="api://66666666-6666-6666-6666-666666666666",
                calibration_reference="offline-unit-test-only-not-measured",
                deployment_version="unit-only", variant=variant, enable_config=True)
            validate_config(config)
            self.assertEqual(len(matching_routes(config, "clinic-b", "exam", "", {})), count)
            self.assertEqual(config["hl7Profiles"]["clinic-a"]["rx"]["doseUnits"], "TESTUNIT")
            self.assertEqual(set(config["directUpload"]["allowedSources"]), {"clinic-a", "clinic-b"})
            for source in ("clinic-a", "clinic-b"):
                self.assertEqual(set(config["hl7Profiles"][source]), set(SPECIFIC))
            if variant == "hl7-error":
                from pipeline.contracts import select_profile
                self.assertEqual(len(select_profile(config, "clinic-b", "exam")["sendingApplication"]), 64)
            self.assertEqual(runner["deniedClientId"], runner["runnerClientId"])

    def test_normalization_preserves_meaning(self):
        self.assertEqual(normal(" TEST   PERSON "), normal("test person"))
        self.assertNotEqual(normal("TEST-1"), normal("TEST1"))
        self.assertNotEqual(normal("0"), normal("1"))

    def test_missing_hl7_fields_cannot_pass(self):
        fields = values("rx", "heldout", 101)
        checks = hl7_assertions("MSH|TEST ONLY\rPID|1\r", fields, "rx")
        self.assertGreater(len(checks), 5)
        self.assertFalse(any(item["passed"] for item in checks))

    def test_run_case_plumbing_without_cloud(self):
        runner = Runner.__new__(Runner)
        runner.state = {"cases": {}}
        runner.config = {"versions": {"test": "local-mock-only"}, "sources": {"one": "clinic-a"}}
        runner.attempt = lambda: None
        runner.save = lambda: None
        runner.upload = lambda *args: None
        runner.wait = lambda fax: {"status": "Completed"}
        runner.inspect = lambda case, job, result: result["assertions"].append({"name": "local-plumbing", "passed": True})
        case = {"id": "local-only", "sha256": "a" * 64, "sourceProfile": "one", "expected": {"state": "complete"}}
        self.assertEqual(runner.run_case(case)["status"], "passed")
        self.assertIn("faxId", runner.state["cases"]["local-only"])

    def test_persistent_post_cap(self):
        path = Path(__file__).parent / "evidence" / "local-report-selfcheck" / "cap.json"
        if path.exists():
            path.unlink()
        journal = Journal(path, "test-only-fingerprint", 1)
        journal.before_post()
        restored = Journal(path, "test-only-fingerprint", 1)
        with self.assertRaises(RuntimeError):
            restored.before_post()
        with self.assertRaises(ValueError):
            Journal(path, "different-fingerprint", 1)

    def test_development_assessment_fixed_threshold_and_truth(self):
        from evaluate_development import assess
        entry = {"kind": "rx", "pages": 1, "fields": values("rx", "development", 1)}
        pages = {"documents": [{"docType": "body", "confidence": .8, "boundingRegions": [{"pageNumber": 1}]}]}
        types = {"documents": [{"docType": "rx", "confidence": .8, "boundingRegions": [{"pageNumber": 1}]}]}
        extraction = {"documents": [{"docType": "learned-rx", "fields": {
            name: {"valueString": value, "confidence": .8} for name, value in entry["fields"].items()}}]}
        self.assertEqual(assess(entry, pages, types, extraction, "learned-rx")["status"], "passed")
        extraction["documents"][0]["fields"]["Dose"] = {"valueNumber": 0.0, "confidence": .8}
        self.assertEqual(assess(entry, pages, types, extraction, "learned-rx")["status"], "passed")
        extraction["documents"][0]["fields"]["Dose"]["confidence"] = .7999
        self.assertEqual(assess(entry, pages, types, extraction, "learned-rx")["status"], "failed")
        extraction["documents"][0]["fields"]["Dose"]["confidence"] = .99
        types["documents"][0]["docType"] = "exam"
        self.assertEqual(assess(entry, pages, types, extraction, "learned-rx")["status"], "failed")

    def test_failed_development_evidence_cannot_enable(self):
        from evaluate_development import calibration_reference, seal
        failed = seal({"status": "failed", "cases": []})
        self.assertNotIn("calibrationId", failed)
        with self.assertRaises(ValueError):
            calibration_reference(failed, {}, b'{"assets":[]}')

    def test_development_evidence_hash_is_content_addressed(self):
        from evaluate_development import seal
        report = {"status": "passed", "cases": [{"status": "passed"}]}
        result = seal(report)
        self.assertTrue(result["calibrationId"].startswith("synthetic-dev-sha256-"))
        self.assertEqual(seal(result), result)
        result["cases"][0]["status"] = "failed"
        self.assertNotEqual(seal(result)["calibrationId"], result["calibrationId"])

    def test_development_reference_binds_models_and_complete_corpus(self):
        from evaluate_development import API, SCOPE, SOURCE, calibration_reference, model_ids, seal
        from run import canonical_bytes
        from fixtures import digest
        promoted = {"runtimeResourceId": "unit-only-resource", "runtimeEndpoint": "unit-only-endpoint",
                    "modelSettings": {"classifier": {"id": "unit-page"},
                                      "faxTypeClassifier": {"id": "unit-type"},
                                      "documentTypes": {kind: {"modelId": "unit-" + kind} for kind in SPECIFIC}}}
        entries = [{"path": f"development/{kind}-{number}.pdf", "sha256": f"{kind}-{number}",
                    "split": "development", "kind": kind, "pages": 1}
                   for kind in SPECIFIC for number in (1, 2)]
        manifest_bytes = canonical_bytes({"assets": entries})
        evidence = seal({"status": "passed", "source": SOURCE, "scope": SCOPE, "apiVersion": API,
                         "threshold": .8, "modelIds": model_ids(promoted),
                         "runtimeResourceId": promoted["runtimeResourceId"],
                         "runtimeEndpoint": promoted["runtimeEndpoint"],
                         "corpusManifestSha256": digest(manifest_bytes),
                         "cases": [{"path": e["path"], "fixtureSha256": e["sha256"], "status": "passed",
                                    "assertions": [{"name": "unit-only", "passed": True}]} for e in entries]})
        self.assertEqual(calibration_reference(evidence, promoted, manifest_bytes), evidence["calibrationId"])
        evidence["cases"][0]["status"] = "failed"
        with self.assertRaises(ValueError):
            calibration_reference(evidence, promoted, manifest_bytes)

    def test_skipped_is_not_pass(self):
        import xml.etree.ElementTree as ET
        directory = Path(__file__).parent / "evidence" / "local-report-selfcheck"
        results = [{"id": "unavailable", "status": "skipped", "reason": "No cloud authorization"},
                   {"id": "waiting", "status": "inconclusive", "reason": "No terminal evidence"}]
        report(directory, results)
        xml = ET.parse(directory / "junit.xml").getroot()
        self.assertEqual(xml.attrib["skipped"], "2")
        self.assertEqual(len(xml.findall("./testcase/skipped")), 2)


if __name__ == "__main__":
    unittest.main()
