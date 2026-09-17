"""Operational facts only: model scores are NOT independent quality ground truth."""

from datetime import datetime
import hashlib
import re

from .contracts import PROFILES
from .policy import MODEL, SEGMENT
from .storage import encode

MODEL_ROLES = ("pageClassifier", "faxTypeClassifier", "extractor")
PHASES = ("prepare", "classify", "removeCovers", "type", "extract", "publish", "review")


def model_ids(values):
    return {key: value for key, value in values.items() if key in MODEL_ROLES
            and isinstance(value, str) and MODEL.fullmatch(value)}


def timings(value):
    result = {}
    if not isinstance(value, dict):
        return result
    for phase in PHASES:
        if not isinstance(value.get(phase), dict):
            continue
        kept = {}
        for event in ("startedAt", "completedAt", "decisionAt", "preparedAt"):
            stamp = value[phase].get(event)
            try:
                if not isinstance(stamp, str) or len(stamp) > 40 or datetime.fromisoformat(stamp).tzinfo is None:
                    continue
                kept[event] = stamp
            except ValueError:
                continue
        if kept:
            result[phase] = kept
    return result


def facts(job):
    result = {key: job[key] for key in ("faxId", "status", "createdAt", "updatedAt")}
    for key in ("completionMode", "reviewRevision", "reason", "documentType",
                "pageCount", "bodyPageCount", "coverPageCount", "fieldMinimumConfidence",
                "faxTypeConfidence", "reviewEnteredAt", "completionPreparedAt",
                "completionPreparationRevision"):
        if key in job:
            result[key] = job[key]
    result["schemaVersion"] = 1
    result["qualityTruth"] = "not-measured; requires independent labeled evaluation"
    result["ingestionChannel"] = job.get("provenance", {}).get("channel", "authenticated-http")
    result["originalReady"] = job.get("originalReady") is True
    result["manifestReady"] = job.get("publication", {}).get("manifestReady") is True
    result["reviewETag"] = review_etag(job)
    if job["status"] == "Completed":
        if job.get("completedAtSource") == "manifestLastModified" and "completedAt" in job:
            result["completedAt"] = job["completedAt"]
            result["completedAtSource"] = "manifestLastModified"
        manifest = job.get("manifest", {})
        if manifest.get("container") == "extracted" and isinstance(manifest.get("name"), str):
            result["completionClock"] = {"container": "extracted", "name": manifest["name"],
                                         "property": "lastModified"}
    config = job.get("config", {})
    if config:
        result["configSha256"] = hashlib.sha256(encode(config)).hexdigest()
    allowed = {s for group in config.get("sources", {}).values()
               if isinstance(group, list) for s in group if isinstance(s, str)}
    allowed.update(s for s in config.get("directUpload", {}).get("allowedSources", []) if isinstance(s, str))
    source = job.get("source")
    if isinstance(source, str) and SEGMENT.fullmatch(source) and source in allowed:
        result["sourceLabel"] = source
    kind = job.get("documentType")
    spec = config.get("documentTypes", {}).get(kind, {})
    configured = model_ids({
        "pageClassifier": config.get("classifier", {}).get("id"),
        "faxTypeClassifier": config.get("faxTypeClassifier", {}).get("id"),
        "extractor": spec.get("modelId")})
    if configured:
        result["configuredModelIds"] = configured
        result["documentIntelligenceApiVersion"] = "2024-11-30"
    used = model_ids(job.get("modelIds", {}))
    if used:
        result["modelIds"] = used
    calibration = config.get("faxTypeClassifier", {}).get("calibrationId")
    if isinstance(calibration, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", calibration):
        result["calibrationId"] = calibration
    configured_profile = config.get("hl7Profiles", {}).get(source, {}).get(kind, {})
    if configured_profile.get("profile") in PROFILES.values():
        result["configuredHl7Profile"] = configured_profile["profile"]
    disposition = job.get("hl7Disposition", {})
    if disposition.get("status") in {"Validated", "NotApplicable", "Rejected"}:
        result["hl7Disposition"] = disposition["status"]
        if disposition.get("profile") in PROFILES.values():
            result["hl7Profile"] = disposition["profile"]
        if disposition.get("version") == "2.5.1":
            result["hl7Version"] = "2.5.1"
        if disposition.get("messageType") in {"ORU^R01^ORU_R01", "ORM^O01^ORM_O01", "RDE^O11^RDE_O11"}:
            result["hl7MessageType"] = disposition["messageType"]
    matched = job.get("matchedRouteCount")
    if type(matched) is int and 0 <= matched <= 32:
        result["matchedRouteCount"] = matched
        if job["status"] == "Completed" and result["manifestReady"]:
            result["publishedRouteCount"] = matched
    observed = timings(job.get("phaseTimings", {}))
    if observed:
        result["phaseTimings"] = observed
    return result


def review_etag(job):
    return f'"review-{job.get("reviewRevision", 0)}"'
