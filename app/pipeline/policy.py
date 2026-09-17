"""Pure, fail-closed policy functions, independently testable without Azure."""

import base64
import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass


class Rejected(ValueError):
    """A safe machine-readable reason; never include document values."""


class Conflict(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    tenant: str
    oid: str
    roles: frozenset

    @property
    def key(self):
        return f"{self.tenant}:{self.oid}"


def authenticate(headers, env):
    # These headers are authoritative ONLY behind App Service EasyAuth, which
    # strips/replaces client-supplied identity headers. This is not JWT validation.
    if env.get("WEBSITE_AUTH_ENABLED", "").lower() != "true":
        raise PermissionError("platform_auth_required")
    try:
        principal = json.loads(base64.b64decode(
            headers.get("x-ms-client-principal", ""), validate=True))
        if principal["auth_typ"] != "aad":
            raise ValueError()
        claims = {}
        for claim in principal["claims"]:
            claims.setdefault(claim["typ"], []).append(claim["val"])
        tenant = env["ENTRA_TENANT_ID"]
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        if claims.get("iss") != [issuer] or claims.get("aud") != [env["ENTRA_API_AUDIENCE"]]:
            raise ValueError()
        tids = claims.get("tid", claims.get(
            "http://schemas.microsoft.com/identity/claims/tenantid", []))
        oids = claims.get("oid", claims.get(
            "http://schemas.microsoft.com/identity/claims/objectidentifier", []))
        if tids != [tenant] or len(oids) != 1:
            raise ValueError()
        uuid.UUID(oids[0])
        # Honor both unmapped and EasyAuth's mapped role claim types.
        roles = claims.get("roles", []) + claims.get(
            "http://schemas.microsoft.com/ws/2008/06/identity/claims/role", [])
        return Actor(tenant, oids[0], frozenset(roles))
    except (ValueError, KeyError, TypeError):
        raise PermissionError("invalid_principal") from None


def fax_id(actor, key):
    try:
        canonical = str(uuid.UUID(key))
    except (ValueError, TypeError, AttributeError):
        raise Rejected("invalid_idempotency_key") from None
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fax:{actor.key}:{canonical}"))


def fingerprint(body, source, mime, owner):
    metadata = json.dumps([source, mime, owner], separators=(",", ":")).encode()
    return hashlib.sha256(metadata + b"\0" + body).hexdigest()


def permitted_source(config, actor, source):
    if source not in config["sources"].get(actor.key, []):
        raise PermissionError("source_not_permitted")
    return source


SEGMENT = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
PATH = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\[\*\])?(?:\.[A-Za-z][A-Za-z0-9_-]*(?:\[\*\])?)*$")
MODEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._~-]{1,63}$")


def validate_config(config):
    """An enabled configuration must be explicit; examples cannot enable by accident."""
    if config.get("enabled") is not True or config.get("example") is not False:
        raise Rejected("configuration_disabled")
    classifier = config.get("classifier", {})
    if not MODEL.fullmatch(classifier.get("id", "")):
        raise Rejected("invalid_classifier")
    threshold = classifier.get("minimumConfidence")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not .8 <= threshold <= 1:
        raise Rejected("invalid_classifier_threshold")
    covers, bodies = classifier.get("coverClasses"), classifier.get("bodyClasses")
    types = config.get("documentTypes")
    if not covers or not isinstance(covers, list) or not isinstance(bodies, dict) or not bodies:
        raise Rejected("missing_cover_body_classes")
    if set(covers) & set(bodies) or not isinstance(types, dict) or not types:
        raise Rejected("invalid_document_types")
    for name, spec in types.items():
        if not SEGMENT.fullmatch(name) or not MODEL.fullmatch(spec.get("modelId", "")):
            raise Rejected("invalid_model")
        expected = spec.get("expectedFields")
        if not isinstance(expected, list) or not expected or len(expected) > 200:
            raise Rejected("invalid_expected_schema")
        if any(not isinstance(p, str) or not PATH.fullmatch(p) for p in expected):
            raise Rejected("invalid_expected_path")
        if len(set(expected)) != len(expected):
            raise Rejected("duplicate_expected_path")
        if not isinstance(spec.get("resultDocType"), str) or not spec["resultDocType"]:
            raise Rejected("missing_result_document_type")
    if any(t not in types and not (config.get("schemaVersion") == 2 and t == "body")
           for t in bodies.values()):
        raise Rejected("unknown_body_type")
    sources = config.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise Rejected("missing_sources")
    for identity, allowed in sources.items():
        try:
            tid, oid = identity.split(":")
            uuid.UUID(tid)
            uuid.UUID(oid)
        except (ValueError, AttributeError):
            raise Rejected("invalid_source_identity") from None
        if not isinstance(allowed, list) or not allowed or any(
                not isinstance(s, str) or not SEGMENT.fullmatch(s) for s in allowed):
            raise Rejected("invalid_source")
    rules = config.get("routes")
    if not isinstance(rules, list) or len(rules) > 32:
        raise Rejected("invalid_routes")
    seen = set()
    for rule in rules:
        name = rule.get("id", "")
        match = rule.get("match", {})
        if not SEGMENT.fullmatch(name) or name in seen or not isinstance(match, dict):
            raise Rejected("invalid_route")
        seen.add(name)
        if set(match) - {"source", "documentType", "contentContains", "fieldEquals"}:
            raise Rejected("unknown_route_condition")
        all_sources = {s for v in sources.values() for s in v} | set(
            config.get("directUpload", {}).get("allowedSources", []))
        if "source" in match and match["source"] not in all_sources:
            raise Rejected("unknown_route_source")
        if "documentType" in match and match["documentType"] not in types:
            raise Rejected("unknown_route_type")
        if "contentContains" in match and (not isinstance(match["contentContains"], str) or not match["contentContains"]):
            raise Rejected("invalid_content_condition")
        equals = match.get("fieldEquals", {})
        if not isinstance(equals, dict) or any(not PATH.fullmatch(p) for p in equals):
            raise Rejected("invalid_field_condition")
        for path, value in equals.items():
            relevant = [types[match["documentType"]]] if "documentType" in match else types.values()
            if any(path not in spec["expectedFields"] for spec in relevant):
                raise Rejected("route_field_not_gated")
            if value is None or isinstance(value, (dict, list)):
                raise Rejected("invalid_route_value")
    if config.get("schemaVersion") == 2:
        from .contracts import validate_v2
        validate_v2(config)
    return config


def resolve_fields(fields, path):
    """Expand expected array wildcards; an empty array is a missing required value."""
    nodes = [fields]
    parts = path.split(".")
    for index, part in enumerate(parts):
        array = part.endswith("[*]")
        key = part[:-3] if array else part
        next_nodes = []
        for node in nodes:
            field = node.get(key) if isinstance(node, dict) else None
            if not isinstance(field, dict):
                return []
            if array:
                values = field.get("valueArray")
                if not isinstance(values, list) or not values:
                    return []
                next_nodes.extend(values)
            else:
                next_nodes.append(field)
        nodes = next_nodes
        if any(not isinstance(n, dict) for n in nodes):
            return []
        if index < len(parts) - 1:
            nodes = [n.get("valueObject", {}) for n in nodes]
    return nodes


def value_of(field):
    for key in ("valueString", "valueNumber", "valueInteger", "valueBoolean",
                "valueDate", "valueTime", "valuePhoneNumber", "valueSelectionMark",
                "valueCountryRegion", "valueCurrency", "valueAddress", "valueSignature",
                "valueSelectionGroup"):
        if key in field:
            return field[key]
    return None


def gate(fields, expected):
    if not isinstance(expected, list) or not expected or any(
            not isinstance(p, str) or not PATH.fullmatch(p) for p in expected):
        raise Rejected("invalid_expected_schema")
    scores, values, accepted = {}, {}, True
    numeric_scores, complete_scores, reasons = [], True, {}
    for path in expected:
        nodes = resolve_fields(fields, path)
        scores[path] = [n.get("confidence") for n in nodes]
        values[path] = [value_of(n) for n in nodes]
        reasons[path] = []
        if not nodes:
            accepted = False
            complete_scores = False
            reasons[path].append({"code": "missing_field"})
        for index, node in enumerate(nodes):
            score, value = node.get("confidence"), value_of(node)
            # Exact comparisons, no rounding/averaging/coercion. False and 0 are valid.
            numeric_score = (isinstance(score, (int, float)) and not isinstance(score, bool)
                             and math.isfinite(score) and 0 <= score <= 1)
            valid_score = numeric_score and score >= .80
            if numeric_score:
                numeric_scores.append(score)
            else:
                complete_scores = False
            if not valid_score:
                code = ("missing_confidence" if score is None else
                        "below_threshold" if numeric_score else "invalid_confidence")
                reasons[path].append({"index": index, "code": code})
            valid_value = value is not None and value != "" and value != [] and value != {}
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                valid_value = False
            if "valueCurrency" in node:
                amount = value.get("amount") if isinstance(value, dict) else None
                valid_value = (isinstance(amount, (int, float)) and not isinstance(amount, bool)
                               and math.isfinite(amount))
            if isinstance(value, dict) and not any(v is not None and v != "" for v in value.values()):
                valid_value = False
            if isinstance(value, str) and (not value.strip() or value.strip().lower() in
                                           {"unreadable", "illegible", "unknown", "?"}):
                valid_value = False
            if not valid_value:
                reasons[path].append({"index": index, "code": "invalid_value"})
            accepted = accepted and valid_score and valid_value
    return {"passed": bool(accepted), "scores": scores, "values": values,
            "minimumConfidence": min(numeric_scores) if complete_scores and numeric_scores else None,
            "reasons": {path: issues for path, issues in reasons.items() if issues}}


def page_plan(result, page_count, classifier):
    assignments = {}
    for doc in result.get("documents", []):
        regions = doc.get("boundingRegions", [])
        score, label = doc.get("confidence"), doc.get("docType")
        if (len(regions) != 1 or isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not classifier["minimumConfidence"] <= score <= 1):
            raise Rejected("ambiguous_page")
        page = regions[0].get("pageNumber")
        if type(page) is not int or not 1 <= page <= page_count or page in assignments:
            raise Rejected("ambiguous_page")
        if label not in classifier["coverClasses"] and label not in classifier["bodyClasses"]:
            raise Rejected("unknown_page_class")
        assignments[page] = label
    if set(assignments) != set(range(1, page_count + 1)):
        raise Rejected("unclassified_page")
    body = [p for p in range(1, page_count + 1) if assignments[p] in classifier["bodyClasses"]]
    types = {classifier["bodyClasses"][assignments[p]] for p in body}
    # A fax containing multiple document types needs explicit segmentation policy,
    # not a guess that concatenating unrelated forms is safe.
    if not body or len(types) != 1:
        raise Rejected("empty_or_mixed_body")
    return {"bodyPages": body, "coverPages": sorted(set(assignments) - set(body)),
            "documentType": next(iter(types)), "originalPageCount": page_count}


def matching_routes(config, source, doc_type, content, values):
    matches = []
    for rule in config["routes"]:
        match = rule["match"]
        if "source" in match and match["source"] != source:
            continue
        if "documentType" in match and match["documentType"] != doc_type:
            continue
        if "contentContains" in match and match["contentContains"] not in content:
            continue
        if any(not values.get(p) or any(type(v) is not type(want) or v != want
                                       for v in values[p])
               for p, want in match.get("fieldEquals", {}).items()):
            continue
        matches.append(rule["id"])
    return matches
