#!/bin/bash
set -euo pipefail
set +x
umask 077
exec 9>/opt/fax-test/runner.lock
flock -n 9
python3 - <<'PY' > /opt/fax-test/logs/source-sync.log 2>&1
import base64,hashlib,io,json,os,pathlib,shutil,tempfile,zipfile
root=pathlib.Path("/opt/fax-test/source/Azure-Fax-Production")
assert root.is_dir() and (root/".venv").is_dir()
assert not (root/"artifacts/source-update-incomplete").exists(), "reconcile interrupted source update"
count=int(os.environ.pop("SOURCE_CHUNK_COUNT"))
assert 1<=count<=15
data=base64.b64decode("".join(os.environ.pop(f"SOURCE_ARCHIVE_{i:03d}") for i in range(count)),validate=True)
assert len(data)<=524288 and hashlib.sha256(data).hexdigest()==os.environ.pop("SOURCE_SHA256").lower()
with tempfile.TemporaryDirectory(dir="/opt/fax-test/scratch") as temporary:
    stage=pathlib.Path(temporary)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sum(e.file_size for e in archive.infolist())<=20971520
        for entry in archive.infolist():
            path=pathlib.PurePosixPath(entry.filename)
            assert not path.is_absolute() and ".." not in path.parts and "\\" not in entry.filename
            assert len(path.parts)>=3 and path.parts[0]=="Azure-Fax-Production" and path.parts[1] in {"app","e2e","scripts"}
            assert path.suffix.lower() in {".py",".json",".txt",".sh",".ps1"}
            assert not any(p.startswith(".venv") or p in {"artifacts","generated","evidence","__pycache__",".python_packages"} for p in path.parts)
            assert ((entry.external_attr>>16)&0o170000)!=0o120000
        archive.extractall(stage)
    incoming=stage/"Azure-Fax-Production"
    for folder in ("app","e2e"):
        assert (incoming/folder/"requirements.txt").read_bytes()==(root/folder/"requirements.txt").read_bytes(), "dependency change requires an explicit rebuild"
    marker=root/"artifacts/source-update-incomplete"
    marker.write_text(hashlib.sha256(data).hexdigest())
    for folder in ("app","e2e","scripts"):
        for source in (incoming/folder).rglob("*"):
            if source.is_file():
                target=root/source.relative_to(incoming)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target)
    (root/"artifacts/source-sha256.txt").write_text(hashlib.sha256(data).hexdigest())
    context=json.loads(base64.b64decode(os.environ.pop("RUNNER_CONTEXT"),validate=True))
    (root/"artifacts/private-test.outputs.json").write_text(json.dumps(context))
    marker.unlink()
PY
echo 'Approved source update completed; model state, evidence and environment preserved.'
