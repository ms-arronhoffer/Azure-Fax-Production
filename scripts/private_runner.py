"""Private Linux runner operations. No credentials or document content in stdout."""

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
import zipfile

from azure.identity import ManagedIdentityCredential

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "79398e05-f318-4010-a508-d913c3763e39"
TENANT = "16b3c013-d300-468d-ac64-7eda0820b6d3"
RUNTIME_GROUP = "rg-fax-e2e-westus2"
ARTIFACTS = ROOT / "artifacts"

def config_hash(path):
    value = json.loads(path.read_text())
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def corpus_settings():
    data = json.loads((ARTIFACTS / "training-config.json").read_text())
    version = data.get("corpusVersion")
    if version is not None and not re.fullmatch(r"[a-z0-9-]{1,24}", version):
        raise RuntimeError("Invalid corpus version")
    suffix = "-" + version if version else ""
    return data, ARTIFACTS / ("corpus" + suffix), suffix


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PrivateHttpError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Private operation failed with HTTP {status}; body suppressed")


def request(url, token, method="GET", body=None):
    payload = json.dumps(body).encode() if isinstance(body, dict) else body
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        headers["Content-Type"] = "application/json" if isinstance(body, dict) else "application/zip"
    try:
        with build_opener(NoRedirect).open(Request(url, data=payload, headers=headers, method=method), timeout=180) as response:
            data = response.read()
            if response.status == 202:
                return response.status, response.headers, {}
            return response.status, response.headers, json.loads(data) if data else {}
    except HTTPError as exc:
        raise PrivateHttpError(exc.code) from None


def private_dns(host):
    addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    ranges = [ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if not addresses or any(not any(ipaddress.ip_address(ip) in network for network in ranges) for ip in addresses):
        raise RuntimeError("Data endpoint did not resolve exclusively to RFC1918 IPs")
    return sorted(addresses)


def check_network(values):
    hosts = [
        f"{values['functionAppName']}.azurewebsites.net",
        f"{values['functionAppName']}.scm.azurewebsites.net",
    ]
    for key in ("dataStorageAccountName", "hostStorageAccountName"):
        for service in (("blob", "queue", "table", "file") if key.startswith("host") else ("blob", "queue")):
            hosts.append(f"{values[key]}.{service}.core.windows.net")
    for key in ("runtimeDocumentIntelligenceEndpoint", "authoringDocumentIntelligenceEndpoint", "trainingStorageAccountUrl"):
        hosts.append(urlparse(values[key]).hostname)
    evidence = {host: private_dns(host) for host in hosts}
    (ARTIFACTS / "private-dns-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def package_deploy(values, configuration_directory):
    config = configuration_directory / "runtime-config.json"
    if not config.is_file():
        raise RuntimeError("Missing promoted/validated artifacts/runtime-config.json; train and verify models first")
    if sys.version_info[:2] != (3, 11) or platform.system() != "Linux" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Linux x64 Python 3.11 builder required")
    check_network(values)
    verify_config(values, configuration_directory)
    stage = ARTIFACTS / f"package-stage-{time.time_ns()}"
    stage.mkdir()
    try:
        for source in (ROOT / "app").rglob("*"):
            relative = source.relative_to(ROOT / "app")
            if any(p in {"tests", ".venv", "__pycache__", ".python_packages", ".pytest_cache"} for p in relative.parts):
                continue
            if source.is_symlink():
                raise RuntimeError("No source symlinks allowed")
            if not source.is_file() or not (source.suffix == ".py" or str(relative) in {"host.json", "requirements.txt"}):
                continue
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (stage / "pipeline").mkdir(exist_ok=True)
        shutil.copyfile(config, stage / "pipeline" / "config.json")
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--quiet",
                        "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
                        "--python-version", "3.11", "--implementation", "cp", "--only-binary=:all:",
                        "-r", str(stage / "requirements.txt"), "--target", str(stage / ".python_packages/lib/site-packages")], check=True)
        # Dependency certifi CA PEM bundles are trusted runtime assets, not source-sync material.
        package = ARTIFACTS / "fax-function.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(stage.rglob("*")):
                if item.is_file():
                    archive.write(item, item.relative_to(stage).as_posix())
        with zipfile.ZipFile(package) as archive:
            entries = set(archive.namelist())
            if not {"host.json", "function_app.py", "pipeline/config.json"}.issubset(entries):
                raise RuntimeError("Incomplete Function package")
            if not any(e.startswith(".python_packages/lib/site-packages/azure/functions/") for e in entries):
                raise RuntimeError("Missing Functions dependencies")
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        package.with_suffix(".zip.sha256").write_text(digest, encoding="ascii")
        app_id = values["functionAppResourceId"]
        prefix = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RUNTIME_GROUP}/providers/Microsoft.Web/sites/"
        if not app_id.lower().startswith(prefix.lower()):
            raise RuntimeError("Function is outside approved scope")
        with ManagedIdentityCredential(client_id=values["deployerIdentity"]["clientId"]) as credential:
            from azure.core.exceptions import ResourceExistsError
            from azure.storage.blob import BlobClient, ContentSettings
            package_url = f"https://{values['hostStorageAccountName']}.blob.core.windows.net/packages/{digest}.zip"
            with BlobClient.from_blob_url(package_url, credential=credential) as blob:
                try:
                    blob.upload_blob(package.read_bytes(), overwrite=False,
                                     content_settings=ContentSettings(content_type="application/zip"))
                except ResourceExistsError:
                    if hashlib.sha256(blob.download_blob().readall()).hexdigest() != digest:
                        raise RuntimeError("Immutable deployment package hash conflict") from None
            token = credential.get_token("https://management.azure.com/.default").token
            arm = f"https://management.azure.com{app_id}/config/appsettings"
            _, _, settings = request(f"{arm}/list?api-version=2024-04-01", token, "POST")
            settings["properties"]["PROCESSING_ENABLED"] = "false"
            settings["properties"]["AzureWebJobsFeatureFlags"] = "EnableWorkerIndexing"
            settings["properties"]["WEBSITE_RUN_FROM_PACKAGE"] = package_url
            settings["properties"]["WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID"] = "SystemAssigned"
            settings["properties"].pop("WEBSITES_ENABLE_APP_SERVICE_STORAGE", None)
            for setting in ("WEBSITE_CONTENTAZUREFILECONNECTIONSTRING", "WEBSITE_CONTENTSHARE", "WEBSITE_SKIP_CONTENTSHARE_VALIDATION"):
                settings["properties"].pop(setting, None)
            request(f"{arm}?api-version=2024-04-01", token, "PUT", {"properties": settings["properties"]})
            del settings
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                time.sleep(10)
                token = credential.get_token("https://management.azure.com/.default").token
                try:
                    request(f"https://management.azure.com{app_id}/syncfunctiontriggers?api-version=2024-04-01", token, "POST")
                    _, _, indexed = request(f"https://management.azure.com{app_id}/functions?api-version=2024-04-01", token)
                except PrivateHttpError as error:
                    if error.status not in {400, 409, 500, 502, 503, 504}:
                        raise
                    continue
                names = {item["name"].split("/")[-1] for item in indexed.get("value", [])}
                if len(names) == 13 and {"ready_manifest", "fax_pipeline", "review_fax", "get_facts"} <= names:
                    ready = next(item for item in indexed["value"]
                                 if item["name"].split("/")[-1] == "ready_manifest")
                    bindings = {binding.get("type") for binding in ready.get("properties", {}).get(
                        "config", {}).get("bindings", [])}
                    if bindings != {"eventGridTrigger", "durableClient"}:
                        continue
                    live_path = configuration_directory / "live-config.json"
                    live_settings = json.loads(live_path.read_text())
                    live_settings["versions"]["deployment"] = "sha256:" + digest
                    live_path.write_text(json.dumps(live_settings, indent=2, sort_keys=True) + "\n")
                    (ARTIFACTS / "deployment-evidence.json").write_text(json.dumps({
                        "sha256": digest, "configurationSha256": config_hash(config),
                        "status": "package-ready", "packageUrl": package_url,
                        "indexedFunctions": sorted(names), "processingEnabled": False}), encoding="utf-8")
                    return
            raise RuntimeError("Private managed-identity package host did not index the expected Functions")
    finally:
        shutil.rmtree(stage)


def set_admission(values, enabled):
    app_id = values["functionAppResourceId"]
    prefix = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RUNTIME_GROUP}/providers/Microsoft.Web/sites/"
    if not app_id.lower().startswith(prefix.lower()):
        raise RuntimeError("App settings target outside approved test resource group")
    with ManagedIdentityCredential(client_id=values["deployerIdentity"]["clientId"]) as credential:
        token = credential.get_token("https://management.azure.com/.default").token
        arm = f"https://management.azure.com{app_id}/config/appsettings"
        _, _, settings = request(f"{arm}/list?api-version=2024-04-01", token, "POST")
        settings["properties"]["PROCESSING_ENABLED"] = str(enabled).lower()
        request(f"{arm}?api-version=2024-04-01", token, "PUT", {"properties": settings["properties"]})


def verify_config(values, configuration_directory):
    sys.path.insert(0, str(ROOT / "app"))
    from pipeline.intelligence import Intelligence
    from pipeline.policy import validate_config
    from pipeline.contracts import validate_v2
    data = validate_v2(validate_config(json.loads((configuration_directory / "runtime-config.json").read_text())))
    if data.get("enabled") is not True or data.get("example") is not False:
        raise RuntimeError("A qualified enabled non-example configuration is required")
    direct = data.get("directUpload", {})
    owner = f"{TENANT}:{values['uploaderIdentity']['principalId']}"
    if direct.get("enabled") is not True or direct.get("trustedUploader") != owner:
        raise RuntimeError("Direct upload must bind the configured tenant and uploader object ID")
    sources = direct.get("allowedSources", [])
    if set(sources) != {"clinic-a", "clinic-b"}:
        raise RuntimeError("Standalone synthetic integration must configure both clinic-a and clinic-b")
    if set(data.get("sources", {}).get(owner, [])) != {"clinic-a", "clinic-b"}:
        raise RuntimeError("Integration source map must bind both clinics to the uploader object ID")
    for source in sources:
        if not {"exam", "order", "rx"} <= set(data.get("hl7Profiles", {}).get(source, {})):
            raise RuntimeError("Both clinics require explicit per-type HL7 profile or NotApplicable policies")
    with ManagedIdentityCredential(client_id=values["authorIdentity"]["clientId"]) as credential:
        Intelligence(values["runtimeDocumentIntelligenceEndpoint"], credential).preflight(data)


def train(values, stage):
    config = ARTIFACTS / "training-config.json"
    if not config.is_file():
        raise RuntimeError("Stage explicit training-config via Configure; no guessed model IDs")
    data = json.loads(config.read_text())
    if data["runnerClientId"] != values["authorIdentity"]["clientId"]:
        raise RuntimeError("Training must use author MI, never uploader/runtime MI")
    if data["authoring"]["resourceId"] != values["authoringDocumentIntelligenceResourceId"] or data["runtime"]["resourceId"] != values["runtimeDocumentIntelligenceResourceId"]:
        raise RuntimeError("Training/promotion target mismatch")
    for section, key in (("authoring", "authoringDocumentIntelligenceEndpoint"), ("runtime", "runtimeDocumentIntelligenceEndpoint")):
        if data[section]["endpoint"].rstrip("/") != values[key].rstrip("/"):
            raise RuntimeError("Training endpoint mismatch")
    if not data["trainingContainerUrl"].startswith(values["trainingStorageAccountUrl"].rstrip("/") + "/"):
        raise RuntimeError("Training storage outside isolated authoring account")
    check_network(values)
    deadline = time.monotonic() + 2700
    _, corpus, suffix = corpus_settings()
    count = data.get("trainingCountPerType", 5)
    if type(count) is not int or not 5 <= count <= 15:
        raise RuntimeError("Training count outside approved bounded synthetic range")
    if not (corpus / "manifest.json").exists():
        subprocess.run([sys.executable, "e2e/fixtures.py", "generate", "--root", str(corpus), "--train-count", str(count)], cwd=ROOT, check=True)
    manifest = json.loads((corpus / "manifest.json").read_text())
    training = [asset for asset in manifest["assets"] if asset["split"] == "train"]
    training_pages = sum(asset["pages"] for asset in training)
    unique_layout_pages = sum({asset["sha256"]: asset["pages"] for asset in training}.values())
    if manifest["trainingCountPerType"] != count or training_pages > 10 * count or unique_layout_pages > 4 * count:
        raise RuntimeError("Training corpus exceeds bounded reference/layout counts")
    common = ["--config", str(config), "--root", str(corpus), "--state", str(ARTIFACTS / ("training-state" + suffix + ".json")), "--max-post-attempts", "100"]
    if stage == "promote":
        common += ["--model-output", str(ARTIFACTS / "promoted-model-settings.json")]
    subprocess.run([sys.executable, "e2e/train.py", stage, *common, "--execute"], cwd=ROOT, check=True, timeout=max(1, deadline - time.monotonic()))


def qualify(values, configuration_directory, variant):
    """Use actual promoted models on development data before creating enabled config."""
    check_network(values)
    _, corpus, suffix = corpus_settings()
    promoted = ARTIFACTS / "promoted-model-settings.json"
    evidence = ARTIFACTS / ("development-evidence" + suffix + ".json")
    if not evidence.is_file():
        subprocess.run([
            sys.executable, "e2e/evaluate_development.py", "--config", str(ARTIFACTS / "training-config.json"),
            "--promoted-models", str(promoted), "--root", str(corpus),
            "--state", str(ARTIFACTS / ("development-state" + suffix + ".json")), "--output", str(evidence), "--execute"
        ], cwd=ROOT, check=True, timeout=2700)
    source = hashlib.sha256()
    for path in sorted((ROOT / "app").rglob("*.py")):
        if any(part in {".venv", "__pycache__", ".python_packages", "tests"} for part in path.parts):
            continue
        source.update(path.relative_to(ROOT).as_posix().encode())
        source.update(path.read_bytes())
    configuration_directory.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "e2e/configure_runtime.py", "--promoted-models", str(promoted),
        "--root", str(corpus), "--runtime-output", str(configuration_directory / "runtime-config.json"),
        "--runner-output", str(configuration_directory / "live-config.json"),
        "--tenant-id", TENANT, "--uploader-client-id", values["uploaderIdentity"]["clientId"],
        "--uploader-principal-id", values["uploaderIdentity"]["principalId"],
        "--observer-client-id", values["authorIdentity"]["clientId"],
        "--reviewer-client-id", values["reviewerIdentity"]["clientId"],
        "--storage-url", values["dataStorageAccountUrl"].rstrip("/"),
        "--api-url", f"https://{values['functionAppName']}.azurewebsites.net",
        "--api-audience", f"api://{values['apiClientId']}",
        "--development-evidence", str(evidence),
        "--deployment-version", "source-sha256-" + source.hexdigest(),
        "--variant", "happy" if variant == "baseline" else variant, "--enable-config"
    ], cwd=ROOT, check=True, timeout=120)


def live(values, configuration_directory, variant, cases, protocols):
    set_admission(values, False)
    _, corpus_directory, _ = corpus_settings()
    corpus = json.loads((corpus_directory / "manifest.json").read_text())
    if len(corpus["cases"]) > 22 or sum(case.get("pages", 0) for case in corpus["cases"]) > 27:
        raise RuntimeError("Initial live suite exceeds 22 cases/27 readable pages; review cost before expansion")
    if not cases or not set(cases) <= {case["id"] for case in corpus["cases"]}:
        raise RuntimeError("Explicit compatible case subset is required for each configuration variant")
    required = {"exam-pdf-body" if protocol in {"duplicate", "conflict", "admission-rejection"} else "missing-required" for protocol in protocols}
    if not required <= set(cases):
        raise RuntimeError("Select the prerequisite held-out cases for the requested protocols")
    config = configuration_directory / "live-config.json"
    runtime_config = configuration_directory / "runtime-config.json"
    if not config.is_file() or not runtime_config.is_file():
        raise RuntimeError("Stage explicit validated live/runtime configuration via Configure")
    data = json.loads(config.read_text())
    if data["runnerClientId"] != values["uploaderIdentity"]["clientId"] or data["reviewerClientId"] != values["reviewerIdentity"]["clientId"]:
        raise RuntimeError("Live test identities do not match isolated uploader/reviewer")
    if data["deniedClientId"] != values["uploaderIdentity"]["clientId"]:
        raise RuntimeError("Wrong-role test must use the allowlisted uploader, not an unrelated identity")
    if data.get("observerClientId") != values["authorIdentity"]["clientId"]:
        raise RuntimeError("Output assertions require separate author/observer MI, not broader uploader roles")
    expected_api = f"https://{values['functionAppName']}.azurewebsites.net"
    if data["storageUrl"].rstrip("/") != values["dataStorageAccountUrl"].rstrip("/") or data["apiUrl"].rstrip("/") != expected_api:
        raise RuntimeError("Live endpoints outside the isolated deployment")
    if data["apiAudience"] != f"api://{values['apiClientId']}":
        raise RuntimeError("Live API audience does not match isolated registration")
    if data["runtimeConfigSha256"] != config_hash(runtime_config):
        raise RuntimeError("Live configuration hash does not match staged runtime configuration")
    deployment = json.loads((ARTIFACTS / "deployment-evidence.json").read_text())
    if deployment["configurationSha256"] != data["runtimeConfigSha256"] or deployment["status"] != "package-ready":
        raise RuntimeError("Staged runtime configuration is not the successfully deployed package")
    if data["versions"]["deployment"] != "sha256:" + deployment["sha256"]:
        raise RuntimeError("Live evidence must pin the actual deployed package hash")
    check_network(values)
    verify_config(values, configuration_directory)
    evidence = ARTIFACTS / "live-evidence" / variant
    total_attempts = 0
    current_attempts = 0
    for state_path in (ARTIFACTS / "live-evidence").glob("**/run-state.json"):
        attempts = json.loads(state_path.read_text())["attempts"]
        if type(attempts) is not int or attempts < 0:
            raise RuntimeError("Invalid persistent live attempt ledger")
        total_attempts += attempts
        if state_path.parent == evidence:
            current_attempts = attempts
    if total_attempts >= 30:
        raise RuntimeError("Global 30-attempt synthetic live budget reached across configuration variants")
    common = ["--config", str(config), "--root", str(corpus_directory),
              "--max-attempts", str(current_attempts + 30 - total_attempts), "--timeout", "900", "--execute"]
    common += ["--evidence", str(evidence)]
    deadline = time.monotonic() + 2700
    suite_failed = False
    def run_suite(name, extra):
        nonlocal suite_failed
        destination = evidence / name
        destination.mkdir(parents=True, exist_ok=True)
        for filename in ("evidence.json", "junit.xml"):
            (evidence / filename).unlink(missing_ok=True)
        try:
            completed = subprocess.run(
                [sys.executable, "e2e/run.py", *common, *extra], cwd=ROOT,
                check=False, timeout=max(1, deadline - time.monotonic()))
            suite_failed = suite_failed or completed.returncode != 0
        finally:
            for filename in ("evidence.json", "junit.xml"):
                if (evidence / filename).is_file():
                    shutil.copyfile(evidence / filename, destination / filename)
    def interrupted(_signal, _frame):
        raise RuntimeError("Live qualification interrupted; disabling admission")
    prior_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        set_admission(values, True)
        # Restart after an app-setting change is asynchronous; allow host/listeners to initialize.
        time.sleep(60)
        import requests
        with ManagedIdentityCredential(client_id=values["uploaderIdentity"]["clientId"]) as credential:
            token = credential.get_token(f"api://{values['apiClientId']}/.default").token
            probe = requests.post(expected_api + "/api/faxes", headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/pdf",
                "x-fax-source": "clinic-a", "idempotency-key": "00000000-0000-0000-0000-000000000000"
            }, data=b"", timeout=60, allow_redirects=False)
            if probe.status_code != 413:
                raise RuntimeError(f"runtime_admission_preflight_http_{probe.status_code}")
        case_arguments = [argument for case in cases for argument in ("--case", case)]
        run_suite("cases", case_arguments)
        for protocol in protocols:
            run_suite(protocol, ["--protocol", protocol])
        if suite_failed:
            raise RuntimeError("One or more live qualification suites failed; inspect bounded evidence")
    finally:
        try:
            set_admission(values, False)
        finally:
            signal.signal(signal.SIGTERM, prior_handler)


def admission_probe(values):
    expected_api = f"https://{values['functionAppName']}.azurewebsites.net"
    try:
        set_admission(values, True)
        time.sleep(45)
        import requests
        with ManagedIdentityCredential(client_id=values["uploaderIdentity"]["clientId"]) as credential:
            token = credential.get_token(f"api://{values['apiClientId']}/.default").token
            response = requests.post(expected_api + "/api/faxes", headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/pdf",
                "x-fax-source": "clinic-a",
                "idempotency-key": "00000000-0000-0000-0000-000000000000",
            }, data=b"", timeout=60, allow_redirects=False)
            if response.status_code != 413:
                raise RuntimeError(f"runtime_admission_preflight_http_{response.status_code}")
    finally:
        set_admission(values, False)


def reset_live_state(variant):
    evidence = ARTIFACTS / "live-evidence" / variant
    if not evidence.exists():
        return
    archive = ARTIFACTS / "live-evidence-archive" / f"{variant}-{time.time_ns()}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    evidence.replace(archive)


def main():
    if (ARTIFACTS / "source-update-incomplete").exists():
        raise RuntimeError("Reconcile incomplete source update before running a phase")
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["CheckNetwork", "Qualify", "DeployCode", "Train", "Admission", "ResetLive", "Live", "Disable"], required=True)
    parser.add_argument("--training-stage", choices=["preflight", "prepare", "build", "promote"])
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--protocol", action="append", default=[], choices=["duplicate", "conflict", "admission-rejection", "human-corrected-release", "denied-review", "concurrent-review"])
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", args.variant):
        raise RuntimeError("Invalid configuration variant name")
    configuration_directory = ARTIFACTS if args.variant == "baseline" else ARTIFACTS / "variants" / args.variant
    values = {key: item["value"] for key, item in json.loads((ARTIFACTS / "private-test.outputs.json").read_text()).items()}
    if args.phase == "CheckNetwork":
        check_network(values)
    elif args.phase == "Qualify":
        qualify(values, configuration_directory, args.variant)
    elif args.phase == "DeployCode":
        package_deploy(values, configuration_directory)
    elif args.phase == "Train":
        if not args.training_stage:
            raise RuntimeError("Explicit training stage required")
        train(values, args.training_stage)
    elif args.phase == "Admission":
        admission_probe(values)
    elif args.phase == "ResetLive":
        reset_live_state(args.variant)
    elif args.phase == "Live":
        live(values, configuration_directory, args.variant, args.case, args.protocol)
    elif args.phase == "Disable":
        set_admission(values, False)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                  for frame in traceback.extract_tb(error.__traceback__)[-5:]]
        http = re.fullmatch(r"Private operation failed with HTTP (\d{3}); body suppressed", str(error))
        if http is None:
            http = re.fullmatch(r"document_service_http_(\d{3})", str(error))
        if http is None:
            http = re.fullmatch(r"runtime_admission_preflight_http_(\d{3})", str(error))
        print(json.dumps({"event": "runner_failure", "errorType": type(error).__name__,
                          "frames": frames, "httpStatus": int(http[1]) if http else None}), file=sys.stderr)
        raise SystemExit(1) from None
