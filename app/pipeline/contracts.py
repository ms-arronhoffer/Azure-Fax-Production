"""Shared synthetic fax contract and fail-closed demonstration profile validation."""

from datetime import date
import math
import re

from .policy import Rejected

COMMON_FIELDS = ("PatientId", "PatientName", "DateOfBirth", "EncounterId",
                 "DocumentDate", "ProviderId")
TYPE_FIELDS = {
    "exam": ("ExamName", "ResultText"),
    "order": ("OrderCode", "OrderDescription"),
    "rx": ("MedicationCode", "MedicationName", "Dose", "Route", "Frequency"),
}
PROFILES = {"exam": "demo-oru-r01-v1", "order": "demo-orm-o01-v1", "rx": "demo-rde-o11-v1"}
PROFILE_KEYS = {"profile", "sendingApplication", "sendingFacility",
                "receivingApplication", "receivingFacility", "processingId"}


def expected_fields(document_type):
    if not isinstance(document_type, str) or document_type not in TYPE_FIELDS:
        raise Rejected("unsupported_document_type")
    return list(COMMON_FIELDS + TYPE_FIELDS[document_type])


def result_type_matches(actual, expected, model_id):
    # Copied template models can prefix the metadata docType with their model ID.
    return actual == expected or (isinstance(model_id, str) and actual == f"{model_id}:{expected}")


def extraction_field_types(field):
    if field == "Dose":
        return {"string", "number", "integer"}
    if field in {"DateOfBirth", "DocumentDate"}:
        return {"string", "date"}
    return {"string"}


def valid_confidence(score, minimum=.8):
    return (type(score) in (int, float) and math.isfinite(score)
            and minimum <= score <= 1)


def validate_v2(config):
    if config.get("schemaVersion") != 2:
        raise Rejected("configuration_upgrade_required")
    if set(config.get("documentTypes", {})) != set(TYPE_FIELDS):
        raise Rejected("invalid_document_types")
    classifier = config["classifier"]
    if classifier["coverClasses"] != ["cover"] or classifier["bodyClasses"] != {"body": "body"}:
        raise Rejected("invalid_page_classifier_classes")
    classifier = config.get("faxTypeClassifier", {})
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._~-]{1,63}", classifier.get("id", "")):
        raise Rejected("invalid_fax_type_classifier")
    if not valid_confidence(classifier.get("minimumConfidence")):
        raise Rejected("invalid_fax_type_threshold")
    if classifier.get("classes") != {key: key for key in TYPE_FIELDS}:
        raise Rejected("invalid_fax_type_classes")
    if not isinstance(classifier.get("calibrationId"), str) or not classifier["calibrationId"].strip():
        raise Rejected("missing_calibration_evidence")
    for kind, spec in config["documentTypes"].items():
        if spec["expectedFields"] != expected_fields(kind):
            raise Rejected("canonical_schema_mismatch")
    direct = config.get("directUpload", {})
    if not isinstance(direct, dict) or type(direct.get("enabled")) is not bool:
        raise Rejected("invalid_direct_upload_configuration")
    if direct["enabled"]:
        from .policy import SEGMENT
        import uuid
        try:
            tenant, oid = direct["trustedUploader"].split(":")
            uuid.UUID(tenant)
            uuid.UUID(oid)
        except (KeyError, ValueError, AttributeError):
            raise Rejected("invalid_integration_identity") from None
        allowed = direct.get("allowedSources")
        if (not isinstance(allowed, list) or not allowed or len(allowed) != len(set(allowed))
                or any(not isinstance(s, str) or not SEGMENT.fullmatch(s) for s in allowed)):
            raise Rejected("invalid_direct_upload_sources")
    # Mapping errors are document decisions, not grounds to discard the original.
    # They are checked again for both automatic and human-authorized completion.
    return config


def classify_body(result, page_count, classifier):
    if not isinstance(result, dict):
        raise Rejected("mixed_or_ambiguous_fax_type")
    documents = result.get("documents")
    if not isinstance(documents, list) or len(documents) != 1:
        raise Rejected("mixed_or_ambiguous_fax_type")
    document = documents[0]
    if not isinstance(document, dict):
        raise Rejected("mixed_or_ambiguous_fax_type")
    label = document.get("docType")
    if not isinstance(label, str) or label not in classifier["classes"]:
        raise Rejected("unknown_fax_type")
    score = document.get("confidence")
    if not valid_confidence(score, classifier["minimumConfidence"]):
        raise Rejected("low_fax_type_confidence")
    regions = document.get("boundingRegions", [])
    if not isinstance(regions, list) or any(not isinstance(region, dict) for region in regions):
        raise Rejected("incomplete_fax_type_coverage")
    pages = [region.get("pageNumber") for region in regions]
    if (any(type(p) is not int for p in pages) or len(pages) != len(set(pages))
            or set(pages) != set(range(1, page_count + 1))):
        raise Rejected("incomplete_fax_type_coverage")
    return {"documentType": classifier["classes"][label], "confidence": score,
            "calibrationId": classifier["calibrationId"], "bodyPageCount": page_count}


def validated_values(document_type, values):
    if not isinstance(values, dict) or set(values) != set(expected_fields(document_type)):
        raise Rejected("incomplete_canonical_values")
    normalized = {}
    for field, value in values.items():
        if field in {"PatientId", "PatientName", "EncounterId", "ProviderId"} and isinstance(value, str):
            # DI can insert a spurious boundary inside a run of digits. Correct
            # only that narrow OCR artifact; do not rewrite general clinical text.
            value = re.sub(r"(?<=\d)\s+(?=\d)", "", value)
        if field == "Dose":
            if (type(value) not in (str, int, float)
                    or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", str(value))):
                raise Rejected("invalid_canonical_dose")
        elif not isinstance(value, str):
            raise Rejected("invalid_canonical_value")
        if isinstance(value, str) and (not value.strip() or value.strip().lower() in
                                       {"unknown", "unreadable", "illegible", "?"}):
            raise Rejected("invalid_canonical_value")
        if type(value) in (int, float) and not math.isfinite(value):
            raise Rejected("invalid_canonical_value")
        if isinstance(value, str) and (len(value) > 32000 or any(
                ord(c) < 32 and c not in "\n\r\t" for c in value)):
            raise Rejected("invalid_canonical_value")
        if field in {"DateOfBirth", "DocumentDate"}:
            try:
                if type(value) is not str or date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise Rejected("invalid_canonical_date") from None
        normalized[field] = value
    return normalized


def select_profile(config, source, document_type):
    profiles = config.get("hl7Profiles")
    mapping = profiles.get(source, {}).get(document_type) if isinstance(profiles, dict) else None
    if not isinstance(mapping, dict):
        raise Rejected("missing_hl7_mapping")
    if mapping.get("status") == "NotApplicable":
        if (set(mapping) != {"status", "reason"} or not isinstance(mapping["reason"], str)
                or not mapping["reason"].strip()):
            raise Rejected("invalid_not_applicable_mapping")
        return dict(mapping)
    required = PROFILE_KEYS | ({"doseUnits"} if document_type == "rx" else set())
    if set(mapping) != required or mapping.get("profile") != PROFILES.get(document_type):
        raise Rejected("invalid_hl7_profile")
    if mapping["processingId"] != "T":
        raise Rejected("demo_profile_requires_test_processing")
    for key in required - {"profile", "processingId"}:
        if not isinstance(mapping[key], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", mapping[key]):
            raise Rejected("invalid_hl7_source_configuration")
    return dict(mapping)


def canonical(job, document_type, values, page_plan, mode, revision=0):
    return {
        "schemaVersion": 2, "faxId": job["faxId"], "revision": revision,
        "source": job["source"], "documentType": document_type,
        "completionMode": mode, "values": validated_values(document_type, values),
        "originalPageMap": page_plan,
        "provenance": job.get("provenance", {"channel": "authenticated-http",
                                           "identityMeaning": "authenticated submitting identity"}),
    }


def review_request(body, page_count):
    if not isinstance(body, dict) or set(body) != {"documentType", "bodyPages", "values", "reason"}:
        raise Rejected("invalid_review_request")
    pages = body["bodyPages"]
    if (not isinstance(pages, list) or not pages
            or any(type(p) is not int or not 1 <= p <= page_count for p in pages)
            or pages != sorted(set(pages))):
        raise Rejected("invalid_review_page_plan")
    reason = body["reason"]
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
        raise Rejected("invalid_review_reason")
    values = validated_values(body["documentType"], body["values"])
    return {"bodyPages": pages, "coverPages": sorted(set(range(1, page_count + 1)) - set(pages)),
            "originalPageCount": page_count, "documentType": body["documentType"]}, values
