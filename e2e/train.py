"""Private managed-identity runner; no network access without --execute."""
import argparse
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fixtures import COMMON, SPECIFIC, digest, self_check, write_json

API = "2024-11-30"
MODEL_KEYS = ["page", "type", "exam", "order", "rx"]


def safe_url(value, suffix):
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(suffix):
        raise ValueError("Explicit Azure HTTPS endpoint required")
    if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
        raise ValueError("Credentials, SAS, query, fragment and custom ports are prohibited")
    return value.rstrip("/")


def validate_config(config):
    for name in ["authoring", "runtime"]:
        item = config[name]
        safe_url(item["endpoint"], ".cognitiveservices.azure.com")
        if urlparse(item["endpoint"]).path not in {"", "/"}:
            raise ValueError("DI endpoint must not include a path")
        match = re.fullmatch(r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/[^/]+/providers/"
                             r"Microsoft.CognitiveServices/accounts/([^/]+)", item["resourceId"])
        if not match:
            raise ValueError("Explicit Document Intelligence ARM resource IDs required")
        uuid.UUID(match[1])
        if match[2] != urlparse(item["endpoint"]).hostname.split(".")[0]:
            raise ValueError("Endpoint and resource name disagree")
        for key in MODEL_KEYS:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]{1,63}", item["models"][key]):
                raise ValueError("Explicit valid versioned classifier/model IDs required")
    if config["authoring"]["resourceId"] == config["runtime"]["resourceId"]:
        raise ValueError("Authoring and private runtime must be separate resources")
    safe_url(config["trainingContainerUrl"], ".blob.core.windows.net")
    if not re.fullmatch(r"/[a-z0-9](?:[a-z0-9-]{1,61})[a-z0-9]", urlparse(config["trainingContainerUrl"]).path):
        raise ValueError("Training container URL required")
    uuid.UUID(config["runnerClientId"])
    if config.get("buildMode") not in {"template", "neural"}:
        raise ValueError("buildMode must be template or neural")
    if not 0 < config.get("maxTrainingHours", 0) <= 0.5:
        raise ValueError("maxTrainingHours must be >0 and <=0.5")
    if not re.fullmatch(r"[A-Za-z0-9_-]+/", config["trainingPrefix"]):
        raise ValueError("Unique versioned trainingPrefix ending '/' required")
    return config


def plan(config):
    prefix = config["trainingPrefix"]
    container = config["trainingContainerUrl"]
    models = config["authoring"]["models"]
    source = lambda p: {"azureBlobSource": {"containerUrl": container, "prefix": prefix + p}}
    result = [
        ("page", "documentClassifiers:build", {"classifierId": models["page"], "allowOverwrite": False,
         "docTypes": {label: source(f"train/page/{label}/") for label in ["cover", "body"]}}),
        ("type", "documentClassifiers:build", {"classifierId": models["type"], "allowOverwrite": False,
         "docTypes": {label: source(f"train/type/{label}/") for label in SPECIFIC}}),
    ]
    for kind in SPECIFIC:
        result.append((kind, "documentModels:build", {
            "modelId": models[kind], "buildMode": config["buildMode"], "allowOverwrite": False,
            "maxTrainingHours": config["maxTrainingHours"], **source(f"train/extract/{kind}/")}))
    return result


def promoted_settings(config, state):
    models = state.get("runtimeModels", {})
    if set(models) != set(MODEL_KEYS):
        raise ValueError("All five promoted models must be GET-verified before export")
    settings = {"schemaVersion": 2, "enabled": False, "example": False,
                "classifier": {"id": config["runtime"]["models"]["page"], "minimumConfidence": .8,
                               "coverClasses": ["cover"], "bodyClasses": {"body": "body"}},
                "faxTypeClassifier": {"id": config["runtime"]["models"]["type"], "minimumConfidence": .8,
                                      "calibrationId": "", "classes": {kind: kind for kind in SPECIFIC}},
                "documentTypes": {}}
    for kind in SPECIFIC:
        required = COMMON + SPECIFIC[kind]
        candidates = [name for name, spec in models[kind].get("docTypes", {}).items()
                      if set(required) <= set(spec.get("fieldSchema", {}))]
        if len(candidates) != 1:
            raise ValueError("Promoted extractor docType is missing or ambiguous")
        settings["documentTypes"][kind] = {"modelId": config["runtime"]["models"][kind],
                                           "resultDocType": candidates[0], "expectedFields": required}
    return {"schemaVersion": 1, "apiVersion": API, "runtimeResourceId": config["runtime"]["resourceId"],
            "runtimeEndpoint": config["runtime"]["endpoint"], "modelSettings": settings,
            "activationBlocked": "Merge reviewed routing/source/HL7 settings and independent calibration evidence; explicitly authorize activation."}


class Journal:
    def __init__(self, path, fingerprint, limit=100):
        self.path, self.limit = Path(path), limit
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "fingerprint": fingerprint, "postAttempts": 0, "operations": {}}
        if self.data["fingerprint"] != fingerprint:
            raise ValueError("State belongs to a different config/corpus")

    def save(self):
        staged = self.path.with_suffix(".pending")
        write_json(staged, self.data)
        staged.replace(self.path)

    def before_post(self):
        if self.data["postAttempts"] >= self.limit:
            raise RuntimeError("Persistent POST safety cap reached; no automatic reset")
        self.data["postAttempts"] += 1
        self.save()


class Client:
    def __init__(self, endpoint, credential, journal):
        import requests
        self.http = requests.Session()
        self.endpoint, self.credential, self.journal = endpoint.rstrip("/"), credential, journal

    def request(self, method, path, **kwargs):
        url = path if path.startswith("https://") else (
            self.endpoint + "/documentintelligence/" + path + "?api-version=" + API)
        if urlparse(url).netloc != urlparse(self.endpoint).netloc:
            raise ValueError("Cross-origin operation URL prohibited")
        for attempt in range(4 if method == "GET" else 1):
            token = self.credential.get_token("https://cognitiveservices.azure.com/.default").token
            headers = {"Authorization": "Bearer " + token, **kwargs.pop("headers", {})}
            response = self.http.request(method, url, headers=headers, timeout=(15, 90),
                                         allow_redirects=False, **kwargs)
            if response.status_code in {429, 502, 503, 504} and method == "GET" and attempt < 3:
                time.sleep(min(30, 2 ** attempt * 2))
                continue
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"DI HTTP {response.status_code}; inspect Azure diagnostics (body withheld)")
            return response
        raise RuntimeError("GET retry budget exhausted")

    def operation(self, key, path, **kwargs):
        operations = self.journal.data["operations"]
        previous = operations.get(key)
        if previous and previous["status"] == "succeeded":
            return previous["result"]
        if previous and previous["status"] in {"submitting", "failed", "canceled"}:
            raise RuntimeError(f"Operation {key} requires operator reconciliation; not resubmitting")
        if not previous:
            self.journal.before_post()
            operations[key] = {"status": "submitting", "path": path, "started": time.time()}
            self.journal.save()
            response = self.request("POST", path, **kwargs)
            location = response.headers.get("Operation-Location")
            if not location:
                raise RuntimeError("Missing Operation-Location; reconcile submission manually")
            operations[key].update({"status": "running", "location": location})
            self.journal.save()
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            response = self.request("GET", operations[key]["location"])
            result = response.json()
            status = result.get("status", "").lower()
            operations[key].update({"status": status, "lastPolled": time.time()})
            if status in {"succeeded", "failed", "canceled"}:
                # Analyze/build/copy operation results contain no copy authorization token.
                operations[key]["result"] = result
                self.journal.save()
                if status != "succeeded":
                    raise RuntimeError(f"Operation {key} ended {status}")
                return result
            self.journal.save()
            time.sleep(5)
        raise TimeoutError("Operation still pending; rerun same command/state to resume")


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def ocr_labels(document, fields, geometric, result):
    """Ground generated regions in actual DI OCR; fail rather than invent words."""
    labels = []
    pages = {p["pageNumber"]: p for p in result["analyzeResult"]["pages"]}
    for candidate in geometric["labels"]:
        expected = fields[candidate["label"]]
        region = candidate["value"][0]
        page = pages[region["page"]]
        target = [point for part in candidate["value"] for box in part["boundingBoxes"] for point in box]
        left, top, right, bottom = min(target[::2]), min(target[1::2]), max(target[::2]), max(target[1::2])
        hits = []
        for word in page.get("words", []):
            polygon = [value / (page["width"] if i % 2 == 0 else page["height"])
                       for i, value in enumerate(word["polygon"])]
            x, y = sum(polygon[::2]) / 4, sum(polygon[1::2]) / 4
            if left - .01 <= x <= right + .01 and top - .01 <= y <= bottom + .01:
                hits.append({"page": region["page"], "text": word["content"], "boundingBoxes": [polygon]})
        if normalize(" ".join(h["text"] for h in hits)) != normalize(expected):
            raise ValueError(f"OCR label mismatch: {document}/{candidate['label']}; inspect OCR asset")
        labels.append({"label": candidate["label"], "key": None, "value": hits})
    if {x["label"] for x in labels} != set(fields):
        raise ValueError("Incomplete extraction labels")
    return {"document": document, "labels": labels}


def upload_immutable(container, name, data):
    from azure.core.exceptions import ResourceExistsError
    try:
        container.upload_blob(name, data, overwrite=False, metadata={"sha256": digest(data), "testonly": "true"})
    except ResourceExistsError:
        existing = container.get_blob_client(name)
        if existing.get_blob_properties().size != len(data) or digest(existing.download_blob().readall()) != digest(data):
            raise RuntimeError("Training artifact conflict; use a new versioned prefix") from None


def execute(config, root, state, stage, cap):
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import ContainerClient
    manifest_bytes = (root / "manifest.json").read_bytes()
    fingerprint = digest(json.dumps(config, sort_keys=True).encode() + manifest_bytes)
    journal = Journal(state, fingerprint, cap)
    credential = ManagedIdentityCredential(client_id=config["runnerClientId"])
    author = Client(config["authoring"]["endpoint"], credential, journal)
    runtime = Client(config["runtime"]["endpoint"], credential, journal)
    if stage == "prepare":
        container = ContainerClient.from_container_url(config["trainingContainerUrl"], credential=credential,
                                                       retry_total=3)
        manifest = json.loads(manifest_bytes)
        assets = [item for item in manifest["assets"] if item["split"] == "train"]
        for asset in assets:
            path = root / asset["path"]
            response = author.operation("ocr-" + asset["sha256"], "documentModels/prebuilt-layout:analyze",
                                        data=path.read_bytes(), headers={"Content-Type": "application/pdf"})
            # Complete service response retained, not a fabricated legacy readResults response.
            ocr_path = Path(str(path) + ".ocr.json")
            write_json(ocr_path, response)
            label_path = Path(str(path) + ".labels.json")
            if label_path.exists():
                initial = json.loads(label_path.read_text())
                labels = ocr_labels(path.name, asset["fields"], initial, response)
                write_json(label_path, labels)
            for file in [path, ocr_path] + ([label_path] if label_path.exists() else []):
                name = config["trainingPrefix"] + file.relative_to(root).as_posix()
                upload_immutable(container, name, file.read_bytes())
        for kind in SPECIFIC:
            file = root / f"train/extract/{kind}/fields.json"
            upload_immutable(container, config["trainingPrefix"] + file.relative_to(root).as_posix(), file.read_bytes())
        journal.data["prepared"] = True
        journal.save()
    elif stage == "build":
        if journal.data.get("prepared") is not True:
            raise RuntimeError("Run prepare successfully with this exact state first")
        for key, path, body in plan(config):
            author.operation("build-" + key, path, json=body)
            collection = "documentClassifiers" if key in {"page", "type"} else "documentModels"
            info = author.request("GET", collection + "/" + config["authoring"]["models"][key]).json()
            journal.data.setdefault("authoringModels", {})[key] = info
            if key in SPECIFIC:
                schemas = info.get("docTypes", {})
                required = set(COMMON + SPECIFIC[key])
                if not any(required <= set(value.get("fieldSchema", {})) for value in schemas.values()):
                    raise RuntimeError("Built extractor schema is missing required fields")
            journal.save()
    else:
        for key in MODEL_KEYS:
            if journal.data["operations"].get("build-" + key, {}).get("status") != "succeeded":
                raise RuntimeError("Successful authoring build required for every model")
            collection = "documentClassifiers" if key in {"page", "type"} else "documentModels"
            id_key = "classifierId" if key in {"page", "type"} else "modelId"
            operation_key = "copy-" + key
            if operation_key not in journal.data["operations"]:
                journal.before_post()
                authorization = runtime.request("POST", collection + ":authorizeCopy",
                    json={id_key: config["runtime"]["models"][key], "description": "TEST ONLY synthetic fax"}).json()
                if authorization.get("targetResourceId", "").lower() != config["runtime"]["resourceId"].lower():
                    raise RuntimeError("Copy target resource mismatch")
                try:
                    author.operation(operation_key, collection + "/" + config["authoring"]["models"][key] + ":copyTo",
                                     json=authorization)
                finally:
                    del authorization
            else:
                author.operation(operation_key, collection + "/" + config["authoring"]["models"][key] + ":copyTo")
            info = runtime.request("GET", collection + "/" + config["runtime"]["models"][key]).json()
            journal.data.setdefault("runtimeModels", {})[key] = info
            journal.save()
    return journal.data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["preflight", "prepare", "build", "promote"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--model-output", type=Path,
                        help="Optional verified promotion settings output; never enables runtime")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-post-attempts", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.max_post_attempts <= 100:
        parser.error("POST safety cap must be 1..100")
    configuration = validate_config(json.loads(args.config.read_text()))
    check = self_check(args.root)
    if args.stage == "preflight" or not args.execute:
        corpus = json.loads((args.root / "manifest.json").read_text())
        training = [entry for entry in corpus["assets"] if entry["split"] == "train"]
        unique = {entry["sha256"]: entry["pages"] for entry in training}
        print(json.dumps({"status": "preflight_only", "local": check, "apiVersion": API,
                          "usagePlan": {"trainingDocumentReferences": len(training),
                                        "uniqueLayoutDocuments": len(unique),
                                        "uniqueLayoutPages": sum(unique.values()),
                                        "classifierBuilds": 2, "extractorBuilds": 3,
                                        "copyOperations": 5, "copyAuthorizations": 5,
                                        "plannedPostAttempts": len(unique) + 15,
                                        "postAttemptCap": args.max_post_attempts,
                                        "extractorMaxTrainingHoursTotal": 3 * configuration["maxTrainingHours"]},
                          "buildRequests": plan(configuration), "network": "not_contacted",
                          "prerequisites": ["Private runner MI/RBAC/DNS", "DI system MI trusted-service storage access",
                                            "Human approval before --execute", "Inspect OCR labels before build"]}, indent=2))
    else:
        state = execute(configuration, args.root, args.state, args.stage, args.max_post_attempts)
        if args.stage == "promote" and args.model_output:
            write_json(args.model_output, promoted_settings(configuration, state))
