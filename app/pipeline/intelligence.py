"""2024-11-30 REST API with Entra auth, private DNS and strict polling URL validation."""

import base64
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

import requests

from .policy import Rejected
from .contracts import extraction_field_types
from .storage import endpoint

API = "2024-11-30"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class Intelligence:
    def __init__(self, service_endpoint, identity):
        self.endpoint = endpoint(service_endpoint, "di")
        self.identity = identity

    def request(self, method, url, **kwargs):
        """Never follow a redirect carrying the cognitive-services bearer token."""
        token = self.identity.get_token("https://cognitiveservices.azure.com/.default").token
        with requests.request(method, url, headers={"Authorization": "Bearer " + token},
                              allow_redirects=False, timeout=(10, 60), stream=True, **kwargs) as response:
            if response.status_code not in (200, 202):
                if response.status_code == 401 and method == "GET" and "/analyzeResults/" not in url:
                    try:
                        payload = token.split(".")[1]
                        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                        if not isinstance(claims, dict):
                            raise ValueError("Non-object diagnostic token claims")
                    except (ValueError, IndexError):
                        claims = {}
                    # Operational identity metadata only; never the access token or fax data.
                    logging.error("DI_AUTH_DIAGNOSTIC %s", json.dumps({
                        "oid": claims.get("oid"), "tid": claims.get("tid"), "aud": claims.get("aud"),
                        "status": response.status_code, "claimsAvailable": bool(claims)}))
                # Service error bodies can quote fax text; they must not reach history/logs.
                raise RuntimeError(f"document_service_http_{response.status_code}")
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise Rejected("ai_result_size_limit")
            data = json.loads(content) if content else {}
            return response.status_code, dict(response.headers), data

    def operation_url(self, location, kind, model):
        parsed = urlsplit(location)
        base = urlsplit(self.endpoint)
        expected = f"/documentintelligence/{kind}/{model}/analyzeResults/"
        if (parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.fragment
                or parsed.username or parsed.password or not parsed.path.startswith(expected)
                or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", parsed.path[len(expected):])
                or parse_qs(parsed.query, strict_parsing=True) != {"api-version": [API]}):
            raise Rejected("invalid_operation_location")
        return location

    def start(self, kind, model, body, split="perPage"):
        overload = "classifyDocument" if kind == "documentClassifiers" else "analyzeDocument"
        url = (f"{self.endpoint}/documentintelligence/{kind}/{model}:analyze"
               f"?_overload={overload}&api-version={API}")
        if kind == "documentClassifiers":
            # REST calls this parameter 'split', not the SDK option 'splitMode'.
            if split not in {"perPage", "none", "auto"}:
                raise Rejected("invalid_classifier_split")
            url += "&split=" + split
        status, headers, _ = self.request(
            "POST", url, json={"base64Source": base64.b64encode(body).decode("ascii")})
        if status != 202:
            raise Rejected("invalid_analysis_start")
        location = next((v for k, v in headers.items() if k.lower() == "operation-location"), "")
        return self.operation_url(location, kind, model)

    def poll(self, operation, kind, model):
        _, _, result = self.request("GET", self.operation_url(operation, kind, model))
        state = result.get("status")
        if state not in {"notStarted", "running", "succeeded", "failed"}:
            raise Rejected("invalid_analysis_status")
        return result

    def preflight(self, config):
        classifier = config["classifier"]
        _, _, details = self.request(
            "GET", f"{self.endpoint}/documentintelligence/documentClassifiers/{classifier['id']}?api-version={API}")
        if details.get("apiVersion") != API:
            raise Rejected("classifier_api_version_mismatch")
        required = set(classifier["coverClasses"]) | set(classifier["bodyClasses"])
        if not required <= set(details.get("docTypes", {})):
            raise Rejected("classifier_classes_unavailable")
        if "faxTypeClassifier" in config:
            classifier = config["faxTypeClassifier"]
            _, _, details = self.request(
                "GET", f"{self.endpoint}/documentintelligence/documentClassifiers/{classifier['id']}?api-version={API}")
            if details.get("apiVersion") != API:
                raise Rejected("fax_type_classifier_api_version_mismatch")
            if set(details.get("docTypes", {})) != set(classifier["classes"]):
                raise Rejected("fax_type_classes_unavailable")
        for spec in config["documentTypes"].values():
            _, _, model = self.request(
                "GET", f"{self.endpoint}/documentintelligence/documentModels/{spec['modelId']}?api-version={API}")
            schema = model.get("docTypes", {}).get(spec["resultDocType"], {}).get("fieldSchema", {})
            for path in spec["expectedFields"]:
                validate_schema_path(schema, path)
                if (config.get("schemaVersion") == 2
                        and schema[path].get("type") not in extraction_field_types(path)):
                    raise Rejected("model_schema_type_mismatch")


def validate_schema_path(schema, path):
    leaves = {"string", "date", "time", "phoneNumber", "number", "integer", "selectionMark",
              "countryRegion", "signature", "currency", "address", "boolean", "selectionGroup"}
    parts = path.split(".")
    for index, part in enumerate(parts):
        is_array = part.endswith("[*]")
        node = schema.get(part[:-3] if is_array else part, {})
        if is_array:
            if node.get("type") != "array":
                raise Rejected("model_schema_array_mismatch")
            node = node.get("items", {})
        if not node or (index == len(parts) - 1 and node.get("type") not in leaves):
            raise Rejected("model_schema_field_unavailable")
        if index < len(parts) - 1 and node.get("type") != "object":
            raise Rejected("model_schema_object_mismatch")
        schema = node.get("properties", {})
