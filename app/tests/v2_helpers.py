"""Synthetic-only fixtures; never inject faults or principals into deployed runtime."""

from pipeline.contracts import PROFILES, expected_fields
from tests.test_policy import OWNER


def config():
    profile = {"sendingApplication": "FAXDEMO", "sendingFacility": "SYNTHETIC",
               "receivingApplication": "DEMO", "receivingFacility": "SYNTHETIC", "processingId": "T"}
    return {
        "schemaVersion": 2, "enabled": True, "example": False,
        "classifier": {"id": "mock-pages", "minimumConfidence": .8,
                       "coverClasses": ["cover"], "bodyClasses": {"body": "body"}},
        "faxTypeClassifier": {"id": "mock-types", "minimumConfidence": .8,
                              "calibrationId": "independent-unit-fixture-only",
                              "classes": {"exam": "exam", "order": "order", "rx": "rx"}},
        "documentTypes": {kind: {"modelId": "mock-" + kind, "resultDocType": kind,
                                 "expectedFields": expected_fields(kind)} for kind in PROFILES},
        "sources": {OWNER: ["clinic-a", "clinic-b"]},
        "directUpload": {"enabled": True, "trustedUploader": OWNER, "allowedSources": ["clinic-a", "clinic-b"]},
        "hl7Profiles": {source: {kind: {**profile, "profile": name,
                                        **({"doseUnits": "TESTUNIT"} if kind == "rx" else {})}
                                  for kind, name in PROFILES.items()}
                        for source in ("clinic-a", "clinic-b")},
        "routes": [{"id": "archive", "match": {"source": "clinic-a"}},
                   {"id": "billing", "match": {"documentType": "exam"}}],
    }


def values(kind="exam"):
    value = {name: "synthetic-" + name for name in expected_fields(kind)}
    value.update({"DateOfBirth": "1970-01-01", "DocumentDate": "2026-09-15"})
    if kind == "rx":
        value["Dose"] = 0
    return value


def extraction(kind="exam", confidence=.8):
    return {"content": "sensitive synthetic document text", "tables": [],
            "documents": [{"docType": kind, "fields": {
                field: {("valueNumber" if type(value) in (int, float) else "valueString"): value,
                        "confidence": confidence}
                for field, value in values(kind).items()}}]}


def classification(label, count=1, confidence=.8):
    return {"documents": [{"docType": label, "confidence": confidence,
                           "boundingRegions": [{"pageNumber": p} for p in range(1, count + 1)]}]}
