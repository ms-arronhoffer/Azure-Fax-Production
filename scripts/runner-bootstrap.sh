#!/bin/bash
set -euo pipefail
set +x
umask 077
mkdir -p /opt/fax-test/logs /opt/fax-test/scratch
export TMPDIR=/opt/fax-test/scratch TMP=/opt/fax-test/scratch TEMP=/opt/fax-test/scratch
exec 3>&1
exec > /opt/fax-test/logs/bootstrap.log 2>&1
trap 'printf "Runner bootstrap failed; inspect the private VM log, not ARM stdout.\n" >&3' ERR
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ca-certificates build-essential util-linux
python3 - <<'PY'
import base64, hashlib, io, json, os, pathlib, zipfile
count = int(os.environ.pop("SOURCE_CHUNK_COUNT"))
assert 1 <= count <= 15, "invalid source chunk count"
encoded = "".join(os.environ.pop(f"SOURCE_ARCHIVE_{i:03d}") for i in range(count))
data = base64.b64decode(encoded, validate=True)
assert len(data) <= 524288, "archive too large"
assert hashlib.sha256(data).hexdigest() == os.environ.pop("SOURCE_SHA256").lower(), "archive mismatch"
root = pathlib.Path("/opt/fax-test/source")
root.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    assert sum(e.file_size for e in archive.infolist()) <= 20971520, "expanded archive too large"
    for e in archive.infolist():
        name = pathlib.PurePosixPath(e.filename)
        assert not name.is_absolute() and ".." not in name.parts and "\\" not in e.filename
        assert name.parts[0] == "Azure-Fax-Production" and len(name.parts) >= 3
        assert name.parts[1] in {"app", "e2e", "scripts"}
        assert name.suffix.lower() in {".py", ".json", ".txt", ".sh", ".ps1"}
        assert not any(p.startswith(".venv") or p in {"venv", ".python_packages", "__pycache__", ".pytest_cache", ".git", "artifacts", "generated", "evidence"} for p in name.parts)
        assert not name.name.startswith((".env", "local.settings")) and ".local." not in name.name
        assert ((e.external_attr >> 16) & 0o170000) != 0o120000, "symlink forbidden"
        target = root.joinpath(*name.parts)
        assert not target.exists(), "bootstrap already exists; use a fresh approved runner or explicit cleanup"
    archive.extractall(root)
context = json.loads(base64.b64decode(os.environ.pop("RUNNER_CONTEXT"), validate=True))
artifact = root / "Azure-Fax-Production" / "artifacts"
artifact.mkdir(exist_ok=True)
(artifact / "private-test.outputs.json").write_text(json.dumps(context), encoding="utf-8")
PY
unset SOURCE_CHUNK_COUNT SOURCE_SHA256 RUNNER_CONTEXT
for name in "${!SOURCE_ARCHIVE_@}"; do unset "$name"; done
cd /opt/fax-test/source/Azure-Fax-Production
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --quiet -r app/requirements.txt -r e2e/requirements.txt
printf 'Private runner source and Linux dependencies initialized. No code deployed.\n' >&3
