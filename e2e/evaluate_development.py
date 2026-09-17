"""Bounded real-model development checks; not clinical or population calibration."""
import argparse
import base64
import json
import math
import re
import time
from datetime import date
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from pipeline.contracts import result_type_matches

from fixtures import COMMON, SPECIFIC, digest, self_check, write_json
from run import canonical_bytes, normalized_field
from train import API, Client, Journal, validate_config

THRESHOLD = 0.8
SOURCE = "real-promoted-document-intelligence"
SCOPE = "synthetic-development-only-not-clinical-or-population-calibration"


def model_ids(promoted):
    settings = promoted["modelSettings"]
    return {"page": settings["classifier"]["id"], "type": settings["faxTypeClassifier"]["id"],
            **{kind: settings["documentTypes"][kind]["modelId"] for kind in SPECIFIC}}


def development_entries(manifest):
    entries = [entry for entry in manifest["assets"] if entry["split"] == "development"]
    if (len(entries) != 6 or any(sum(e["kind"] == kind for e in entries) != 2 for kind in SPECIFIC)
            or any(e["pages"] != 1 or not e["path"].startswith("development/") for e in entries)):
        raise ValueError("Initial development qualification requires two one-page development documents per type")
    return entries


def acceptable(score):
    return type(score) in (int, float) and math.isfinite(score) and THRESHOLD <= score <= 1


def assess(entry, page_result, type_result, extraction, result_doc_type, model_id=None):
    checks = []
    check = lambda name, ok: checks.append({"name": name, "passed": bool(ok)})
    pages = page_result.get("documents", [])
    check("page-classifier-body", len(pages) == entry["pages"] and all(
        item.get("docType") == "body" and acceptable(item.get("confidence")) for item in pages))
    coverage = [region.get("pageNumber") for item in pages for region in item.get("boundingRegions", [])]
    check("page-classifier-coverage", sorted(coverage) == list(range(1, entry["pages"] + 1)))
    documents = type_result.get("documents", [])
    check("type-correct", len(documents) == 1 and documents[0].get("docType") == entry["kind"])
    check("type-confidence-at-least-0.8", len(documents) == 1 and acceptable(documents[0].get("confidence")))
    type_pages = [r.get("pageNumber") for d in documents for r in d.get("boundingRegions", [])]
    check("type-whole-body-coverage", sorted(type_pages) == list(range(1, entry["pages"] + 1)))
    extracted = extraction.get("documents", [])
    check("extractor-result-type", len(extracted) == 1 and result_type_matches(
        extracted[0].get("docType"), result_doc_type, model_id))
    fields = extracted[0].get("fields", {}) if len(extracted) == 1 else {}
    actual = {}
    for name in COMMON + SPECIFIC[entry["kind"]]:
        field = fields.get(name, {})
        candidates = [field[key] for key in ["valueString", "valueDate", "valueNumber", "valueInteger"] if key in field]
        value = candidates[0] if len(candidates) == 1 else None
        actual[name] = {"value": value, "confidence": field.get("confidence")}
        valid_type = type(value) is str if name != "Dose" else type(value) in (str, int, float)
        if name == "Dose":
            valid_type = valid_type and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", str(value)) is not None
        if name in {"DateOfBirth", "DocumentDate"} and valid_type:
            try:
                valid_type = date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid_type = False
        check("field-type:" + name, valid_type)
        check("field-value:" + name, valid_type and
              normalized_field(name, value) == normalized_field(name, entry["fields"][name]))
        check("field-confidence:" + name, acceptable(field.get("confidence")))
    return {"assertions": checks, "actualFields": actual,
            "actualPageClassification": pages, "actualTypeClassification": documents,
            "status": "passed" if all(item["passed"] for item in checks) else "failed"}


def seal(report):
    report = dict(report)
    report.pop("calibrationId", None)
    if report["status"] == "passed":
        report["calibrationId"] = "synthetic-dev-sha256-" + digest(canonical_bytes(report))
    return report


def calibration_reference(report, promoted, manifest_bytes):
    manifest = json.loads(manifest_bytes)
    entries = development_entries(manifest)
    expected = {entry["path"]: entry["sha256"] for entry in entries}
    actual = {row["path"]: row["fixtureSha256"] for row in report.get("cases", [])}
    if (report.get("status") != "passed" or report.get("source") != SOURCE or report.get("scope") != SCOPE
            or report.get("apiVersion") != API or report.get("threshold") != THRESHOLD
            or report.get("modelIds") != model_ids(promoted)
            or report.get("runtimeResourceId") != promoted["runtimeResourceId"]
            or report.get("runtimeEndpoint") != promoted["runtimeEndpoint"]
            or report.get("corpusManifestSha256") != digest(manifest_bytes)
            or not expected or actual != expected or len(report.get("cases", [])) != len(entries)
            or any(row.get("status") != "passed" or not row.get("assertions")
                   or not all(a.get("passed") is True for a in row["assertions"]) for row in report["cases"])):
        raise ValueError("Development evidence failed, incomplete, mismatched, or not real-model evidence")
    if report.get("calibrationId") != seal(report).get("calibrationId"):
        raise ValueError("Development evidence content digest mismatch")
    return report["calibrationId"]


def evaluate(config, promoted, root, state_path, output):
    from azure.identity import ManagedIdentityCredential
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    entries = development_entries(manifest)
    if model_ids(promoted) != config["runtime"]["models"] or promoted["runtimeEndpoint"] != config["runtime"]["endpoint"]:
        raise ValueError("Promoted models do not match the actual training configuration runtime target")
    fingerprint = digest(canonical_bytes(config) + canonical_bytes(promoted) + manifest_bytes)
    if output.exists():
        existing = json.loads(output.read_text())
        if existing.get("status") == "passed":
            calibration_reference(existing, promoted, manifest_bytes)
            return existing
    journal = Journal(state_path, fingerprint, limit=24)
    client = Client(config["runtime"]["endpoint"],
                    ManagedIdentityCredential(client_id=config["runnerClientId"]), journal)
    report = {"schemaVersion": 1, "source": SOURCE, "scope": SCOPE, "apiVersion": API,
              "threshold": THRESHOLD, "status": "running", "modelIds": model_ids(promoted),
              "runtimeResourceId": promoted["runtimeResourceId"], "runtimeEndpoint": promoted["runtimeEndpoint"],
              "corpusManifestSha256": digest(manifest_bytes), "cases": [],
              "postAttemptCap": 24, "noHeldoutTuning": True}
    write_json(output, report)
    for entry in entries:
        start = time.monotonic()
        row = {"path": entry["path"], "fixtureSha256": entry["sha256"], "expectedType": entry["kind"],
               "expectedFields": entry["fields"], "status": "failed", "assertions": []}
        try:
            raw = (root / entry["path"]).read_bytes()
            if digest(raw) != entry["sha256"]:
                raise ValueError("Development fixture hash mismatch")
            ids = report["modelIds"]
            payloads = {}
            for phase, collection, model, split in [
                ("page", "documentClassifiers", ids["page"], "perPage"),
                ("type", "documentClassifiers", ids["type"], "auto"),
                ("extract", "documentModels", ids[entry["kind"]], None),
            ]:
                options = {"_overload": "classifyDocument" if split else "analyzeDocument"}
                if split:
                    options["split"] = split
                # A new key preserves the six failed binary-request attempts in
                # the journal; the total cap includes those attempts, never resets.
                payload = client.operation("dev-json-" + entry["sha256"] + "-" + phase,
                                           collection + "/" + model + ":analyze",
                                           json={"base64Source": base64.b64encode(raw).decode("ascii")},
                                           params=options)
                payloads[phase] = payload["analyzeResult"]
            row.update(assess(entry, payloads["page"], payloads["type"], payloads["extract"],
                              promoted["modelSettings"]["documentTypes"][entry["kind"]]["resultDocType"],
                              promoted["modelSettings"]["documentTypes"][entry["kind"]]["modelId"]))
            row["resultSha256"] = {phase: digest(canonical_bytes(value)) for phase, value in payloads.items()}
        except Exception as error:
            row["assertions"].append({"name": "real-model-evaluation-completed", "passed": False,
                                      "errorType": type(error).__name__})
        row["elapsedSeconds"] = round(time.monotonic() - start, 3)
        report["cases"].append(row)
        report["postAttempts"] = journal.data["postAttempts"]
        write_json(output, report)
    report["status"] = "passed" if all(row["status"] == "passed" for row in report["cases"]) else "failed"
    report = seal(report)
    write_json(output, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Same actual training.json; author MI must access promoted models")
    parser.add_argument("--promoted-models", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text()))
    promoted = json.loads(args.promoted_models.read_text())
    self_check(args.root)
    development_entries(json.loads((args.root / "manifest.json").read_text()))
    if not args.execute:
        print(json.dumps({"status": "not_run", "network": "not_contacted", "split": "development",
                          "documents": 6, "classifierPagePasses": 12, "customPages": 6,
                          "postAttemptCap": 24, "threshold": THRESHOLD, "modelIds": model_ids(promoted)}))
    else:
        result = evaluate(config, promoted, args.root, args.state, args.output)
        print(json.dumps({"status": result["status"], "calibrationId": result.get("calibrationId"),
                          "scope": SCOPE, "cases": len(result["cases"])}))
        raise SystemExit(0 if result["status"] == "passed" else 1)
