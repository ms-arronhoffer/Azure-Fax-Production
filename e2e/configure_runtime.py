"""Generate local, validated synthetic configuration; never calls Azure or enables the app."""
import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

from fixtures import COMMON, SPECIFIC, digest, write_json
from run import canonical_bytes, validate as validate_runner
from train import API, safe_url

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from pipeline.contracts import PROFILES, select_profile
from pipeline.policy import validate_config

VARIANTS = ["happy", "no-route", "multi-route", "hl7-not-applicable", "hl7-error"]


def synthesize(promoted, manifest, *, tenant_id, uploader_client_id, uploader_principal_id,
               reader_client_id, reviewer_client_id, storage_url, api_url, api_audience,
               calibration_reference, deployment_version, variant, enable_config=False):
    for identity in [tenant_id, uploader_client_id, uploader_principal_id, reader_client_id, reviewer_client_id]:
        if str(uuid.UUID(identity)) != identity:
            raise ValueError("Explicit canonical Azure tenant/client/principal UUID required")
    if not calibration_reference.strip() or not deployment_version.strip():
        raise ValueError("Independent calibration reference and actual deployment version required")
    if variant not in VARIANTS or promoted["apiVersion"] != API:
        raise ValueError("Unsupported variant or promoted API version")
    safe_url(promoted["runtimeEndpoint"], ".cognitiveservices.azure.com")
    config = copy.deepcopy(promoted["modelSettings"])
    if set(config["documentTypes"]) != set(SPECIFIC):
        raise ValueError("Exactly three promoted extraction types required")
    for kind in SPECIFIC:
        if config["documentTypes"][kind]["expectedFields"] != COMMON + SPECIFIC[kind]:
            raise ValueError("Promoted fields do not match canonical ordered schema")
    config.update(schemaVersion=2, enabled=True, example=False)
    config["faxTypeClassifier"]["calibrationId"] = calibration_reference
    integration = tenant_id + ":" + uploader_principal_id
    config["sources"] = {integration: ["clinic-a", "clinic-b"]}
    config["directUpload"] = {"enabled": True, "trustedUploader": integration,
                              "allowedSources": ["clinic-a", "clinic-b"]}
    profile = {"sendingApplication": "FAXDEMO", "sendingFacility": "SYNTHETIC",
               "receivingApplication": "DEMO", "receivingFacility": "SYNTHETIC", "processingId": "T"}
    config["hl7Profiles"] = {source: {kind: {
        **profile, "profile": PROFILES[kind], **({"doseUnits": "TESTUNIT"} if kind == "rx" else {})
    } for kind in SPECIFIC} for source in ["clinic-a", "clinic-b"]}
    config["routes"] = [{"id": source + "-" + kind + "-archive",
                         "match": {"source": source, "documentType": kind}}
                        for source in ["clinic-a", "clinic-b"] for kind in SPECIFIC]
    if variant == "no-route":
        config["routes"] = [r for r in config["routes"] if r["match"]["source"] == "clinic-a"]
    elif variant == "multi-route":
        config["routes"].extend({"id": "clinic-b-" + kind + "-second",
                                  "match": {"source": "clinic-b", "documentType": kind}} for kind in SPECIFIC)
    elif variant == "hl7-not-applicable":
        config["hl7Profiles"]["clinic-b"]["exam"] = {"status": "NotApplicable",
                                                     "reason": "TEST ONLY explicit JSON-only qualification variant"}
    elif variant == "hl7-error":
        # Admitted configuration, but exceeds the strict HL7 HD namespace IS length.
        config["hl7Profiles"]["clinic-b"]["exam"]["sendingApplication"] = "T" * 64
    validate_config(config)
    for source in ["clinic-a", "clinic-b"]:
        for kind in SPECIFIC:
            select_profile(config, source, kind)
    config["enabled"] = enable_config
    source_map = {"one": "clinic-a", "none": "clinic-b", "multi": "clinic-b",
                  "hl7-not_applicable": "clinic-b", "hl7-error": "clinic-b"}
    selected = {
        "happy": [c["id"] for c in manifest["cases"] if c["sourceProfile"] == "one"],
        "no-route": ["routes-none"], "multi-route": ["routes-multi"],
        "hl7-not-applicable": ["hl7-not-applicable"], "hl7-error": ["hl7-error"],
    }[variant]
    known_cases = {case["id"] for case in manifest["cases"]}
    if not selected or set(selected) - known_cases:
        raise ValueError("Corpus does not include the selected variant cases")
    runner = {
        "runnerClientId": uploader_client_id, "observerClientId": reader_client_id,
        "reviewerClientId": reviewer_client_id, "deniedClientId": uploader_client_id,
        "storageUrl": storage_url, "apiUrl": api_url, "apiAudience": api_audience,
        "runtimeConfigSha256": digest(canonical_bytes(config)), "sources": source_map,
        "versions": {"pageClassifier": config["classifier"]["id"],
                     "typeClassifier": config["faxTypeClassifier"]["id"],
                     **{kind + "Model": config["documentTypes"][kind]["modelId"] for kind in SPECIFIC},
                     "deployment": deployment_version, "calibration": calibration_reference},
        "selectedCases": selected, "configurationVariant": variant,
    }
    validate_runner(runner)
    return config, runner


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promoted-models", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime-output", type=Path, required=True)
    parser.add_argument("--runner-output", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--uploader-client-id", required=True)
    parser.add_argument("--uploader-principal-id", required=True)
    parser.add_argument("--observer-client-id", "--reader-client-id", dest="reader_client_id", required=True)
    parser.add_argument("--reviewer-client-id", required=True)
    parser.add_argument("--storage-url", required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--api-audience", required=True)
    evidence = parser.add_mutually_exclusive_group(required=True)
    evidence.add_argument("--development-evidence", type=Path,
                          help="Passed hash-verified evaluate_development.py output for these models/corpus")
    evidence.add_argument("--calibration-reference",
                          help="Planning reference only; cannot be used with --enable-config")
    parser.add_argument("--deployment-version", required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--enable-config", action="store_true",
                        help="Set local config enabled:true; does not change deployed PROCESSING_ENABLED")
    args = parser.parse_args()
    promoted = json.loads(args.promoted_models.read_text())
    manifest_bytes = (args.root / "manifest.json").read_bytes()
    if args.development_evidence:
        from evaluate_development import calibration_reference
        reference = calibration_reference(json.loads(args.development_evidence.read_text()), promoted, manifest_bytes)
    else:
        if args.enable_config:
            parser.error("--enable-config requires passed --development-evidence; a reference alone is insufficient")
        reference = args.calibration_reference
    config, runner = synthesize(
        promoted, json.loads(manifest_bytes),
        tenant_id=args.tenant_id, uploader_client_id=args.uploader_client_id,
        uploader_principal_id=args.uploader_principal_id, reader_client_id=args.reader_client_id,
        reviewer_client_id=args.reviewer_client_id, storage_url=args.storage_url,
        api_url=args.api_url, api_audience=args.api_audience,
        calibration_reference=reference, deployment_version=args.deployment_version,
        variant=args.variant, enable_config=args.enable_config)
    write_json(args.runtime_output, config)
    write_json(args.runner_output, runner)
    print(json.dumps({"status": "local_configuration_generated", "network": "not_contacted",
                      "enabledInFile": config["enabled"], "appActivation": "not_changed",
                      "runtimeConfigSha256": runner["runtimeConfigSha256"], "selectedCases": runner["selectedCases"]}))
