"""Event Grid commit-marker admission, trusted by container RBAC, not blob metadata."""

import hashlib
import json
import re

from .documents import inspect_type
from .policy import Rejected, fingerprint
from .storage import checked_id, encode

MAX_MANIFEST_BYTES = 16384


def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise Rejected("duplicate_json_key")
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(Rejected("invalid_json_number")))
    except (TypeError, ValueError, UnicodeDecodeError):
        raise Rejected("invalid_json") from None


def manifest(raw, trigger_name, config):
    if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES:
        raise Rejected("invalid_ready_size")
    value = strict_json(raw)
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "faxId", "source", "mime", "original", "sha256"}:
        raise Rejected("invalid_ready_manifest")
    try:
        fax = checked_id(value["faxId"])
    except (ValueError, TypeError, AttributeError):
        raise Rejected("invalid_fax_id") from None
    if trigger_name != f"ready/{fax}.json" or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise Rejected("ready_identity_mismatch")
    if value["original"] != {"container": "incoming", "name": f"{fax}/original"}:
        raise Rejected("unsafe_original_location")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise Rejected("invalid_original_digest")
    if value["mime"] not in {"application/pdf", "image/tiff"}:
        raise Rejected("unsupported_media_type")
    direct = config.get("directUpload", {})
    if direct.get("enabled") is not True or not direct.get("trustedUploader"):
        raise Rejected("direct_upload_disabled")
    if value["source"] not in direct.get("allowedSources", []):
        raise Rejected("direct_source_not_allowed")
    return value


def ingest(store, raw, trigger_name, config, maximum, enqueue=True):
    value = manifest(raw, trigger_name, config)
    fax = value["faxId"]
    # A missing original fails BEFORE job/outbox creation. Bounded reads prevent
    # an integration's oversized upload from exhausting the worker.
    original = store.read_bounded("incoming", f"{fax}/original", maximum)
    if not original or hashlib.sha256(original).hexdigest() != value["sha256"]:
        raise Rejected("original_digest_mismatch")
    inspect_type(original, value["mime"])
    owner = config["directUpload"]["trustedUploader"]
    metadata = {
        "owner": owner, "source": value["source"], "mime": value["mime"],
        "fingerprint": fingerprint(original, value["source"], value["mime"], owner),
        "admissionSha256": hashlib.sha256(encode(value)).hexdigest(),
        "provenance": {"channel": "trusted-integration-blob", "originalSha256": value["sha256"],
                       "readyManifest": {"container": "ready", "name": f"{fax}.json"},
                       "configuredIntegration": owner,
                       "identityMeaning": "container-RBAC integration boundary; not end-user identity"},
    }
    return store.intake(fax, original, metadata, config, enqueue=enqueue)


def record_rejection(store, raw, trigger_name, config, reason):
    """Immutable evidence of a rejected attempt, not a claim about later retries."""
    match = re.fullmatch(r"ready/([0-9a-f-]{36})\.json", trigger_name or "")
    if not match:
        return
    try:
        fax = checked_id(match[1])
    except ValueError:
        return
    digest = hashlib.sha256(raw).hexdigest()
    name = f"_admission-rejections/{fax}/{digest}.json"
    if store.exists("jobs", name):
        return
    evidence = {
        "schemaVersion": 1, "faxId": fax, "status": "RejectedAttempt",
        "reason": reason if re.fullmatch(r"[a-z0-9_]{1,80}", reason) else "admission_rejected",
        "manifestSha256": digest, "hashedBytes": len(raw),
        "digestScope": "bounded-trigger-read" if len(raw) > MAX_MANIFEST_BYTES else "entire-manifest",
        "configuredIntegration": config["directUpload"]["trustedUploader"],
        "configSha256": hashlib.sha256(encode(config)).hexdigest(),
        "identityMeaning": "configured container-RBAC boundary; not authenticated end-user metadata",
    }
    store.immutable_json("jobs", name, evidence)
