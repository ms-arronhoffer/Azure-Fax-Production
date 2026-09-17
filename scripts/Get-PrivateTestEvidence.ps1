#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$OutputsFile = (Join-Path $PSScriptRoot '..\artifacts\private-test.outputs.json'),
    [string]$OutputFile = (Join-Path $PSScriptRoot '..\artifacts\live-summary.json'),
    [switch]$IncludeHostLogs,
    [switch]$FieldDetails,
    [switch]$DeploymentApproved
)
$ErrorActionPreference = 'Stop'
if (-not $DeploymentApproved) { throw 'Approved private VM command execution is required.' }
& (Join-Path $PSScriptRoot 'Clear-CompletedTestCommands.ps1') -DeploymentApproved
$outputs = Get-Content $OutputsFile -Raw | ConvertFrom-Json -AsHashtable
$vmId = $outputs.runnerResourceId.value
$subscription = '79398e05-f318-4010-a508-d913c3763e39'
if ($vmId -notmatch "^/subscriptions/$subscription/resourceGroups/rg-fax-e2e-westus2/providers/Microsoft.Compute/virtualMachines/") {
    throw 'Runner outside approved scope.'
}
$script = @'
#!/bin/bash
set -euo pipefail
set +x
/opt/fax-test/source/Azure-Fax-Production/.venv/bin/python - <<'PY'
import json,re
from pathlib import Path
root=Path("/opt/fax-test/source/Azure-Fax-Production/artifacts")
result={}
configuration=root/"training-config.json"
suffix=""
if configuration.exists():
    version=json.loads(configuration.read_text()).get("corpusVersion")
    if version:
        assert re.fullmatch(r"[a-z0-9-]{1,24}",version)
        suffix="-"+version
path=root/"private-dns-evidence.json"
if path.exists():
    data=json.loads(path.read_text())
    result["privateDnsHostsVerified"]=len(data)
path=root/("training-state"+suffix+".json")
if path.exists():
    data=json.loads(path.read_text())
    result["training"]={"prepared":data.get("prepared",False),"postAttempts":data.get("postAttempts",0),
                        "operations":{k:v["status"] for k,v in data.get("operations",{}).items()}}
path=root/("development-evidence"+suffix+".json")
if path.exists():
    data=json.loads(path.read_text())
    result["development"]={"status":data["status"],"calibrationId":data.get("calibrationId"),
                           "cases":[{"path":c["path"],"status":c["status"],
                                     "failedChecks":[a["name"] for a in c.get("assertions",[]) if not a["passed"]],
                                     "typeScores":[{"type":d.get("docType"),"score":d.get("confidence")} for d in c.get("actualTypeClassification",[])],
                                     "patientIdScore":c.get("actualFields",{}).get("PatientId",{}).get("confidence")}
                                    for c in data.get("cases",[])]}
path=root/"deployment-evidence.json"
if path.exists():
    result["deployment"]=json.loads(path.read_text())
result["live"]=[]
for path in sorted((root/"live-evidence").glob("**/evidence.json")):
    if len(path.relative_to(root).parts)==3:
        continue
    data=json.loads(path.read_text())
    result["live"].append({"path":str(path.relative_to(root)),
      "counts":{s:sum(c["status"]==s for c in data.get("cases",[])) for s in ("passed","failed","inconclusive","skipped","not_run")},
      "failures":[{"id":c["id"],"status":c["status"],
               "reason":c.get("reason"),
               "faxId":c.get("faxId"),
               "fieldMismatches":{a["name"][6:]:{
                   "expected":c.get("expected",{}).get("fields",{}).get(a["name"][6:]),
                   "actual":c.get("actual",{}).get("fields",{}).get(a["name"][6:])}
                   for a in c.get("assertions",[])
                   if a["name"].startswith("field:") and not a.get("passed",False)},
                "failedChecks":[a["name"] for a in c.get("assertions",[]) if not a.get("passed",False)]}
               for c in data.get("cases",[]) if c["status"]!="passed"][:5]})
result["diagnostics"]={}
from azure.identity import ManagedIdentityCredential
import requests
values={k:v["value"] for k,v in json.loads((root/"private-test.outputs.json").read_text()).items()}
with ManagedIdentityCredential(client_id=values["deployerIdentity"]["clientId"]) as credential:
    token=credential.get_token("https://management.azure.com/.default").token
    response=requests.get("https://"+values["functionAppName"]+".scm.azurewebsites.net/api/vfs/site/wwwroot/",
                          headers={"Authorization":"Bearer "+token},allow_redirects=False,timeout=30)
    result["hostInspection"]={"scmStatus":response.status_code}
    if response.status_code==200:
        result["hostInspection"]["rootFiles"]=[item["name"] for item in response.json()]
    logs=requests.get("https://"+values["functionAppName"]+".scm.azurewebsites.net/api/vfs/LogFiles/",
                     headers={"Authorization":"Bearer "+token},allow_redirects=False,timeout=30)
    if logs.status_code==200:
        result["hostInspection"]["logFiles"]=[item["name"] for item in logs.json()]
        docker=[item["name"] for item in logs.json() if re.fullmatch(r"[A-Za-z0-9_]+_docker\.log",item["name"])]
        if __INCLUDE_LOGS__ and docker:
            selected=sorted([name for name in docker if "_default_docker.log" in name] or docker)[-1]
            result["hostInspection"]["selectedLog"]=selected
            detail=requests.get("https://"+values["functionAppName"]+".scm.azurewebsites.net/api/vfs/LogFiles/"+selected,
                                headers={"Authorization":"Bearer "+token},allow_redirects=False,timeout=30)
            if detail.status_code==200:
                safe=[line for line in detail.text.splitlines() if not re.search(r"AccountKey=|Bearer |sig=|password=",line,re.I)]
                failures=[line for line in safe if re.search(r"Error|Exception|unable|failed",line,re.I) and "AlreadyExists" not in line]
                stages=[line for line in safe if "FAX_STAGE_FAILURE" in line or "DI_AUTH_DIAGNOSTIC" in line]
                if stages:
                    failures=stages
                result["hostInspection"]["triggerDiagnostics"]={key:detail.text.count(key) for key in ("ready_admission_failed","processing_disabled","AuthorizationPermissionMismatch","listener")}
                result["hostInspection"]["dockerTail"]=[line[-1000:] for line in (failures[-2:] or safe[-4:])]
                result["hostInspection"]["dockerTailTruncated"]=True
    invocation_logs=requests.get(
        "https://"+values["functionAppName"]+".scm.azurewebsites.net/api/vfs/LogFiles/Application/Functions/Function/ready_manifest/",
        headers={"Authorization":"Bearer "+token},allow_redirects=False,timeout=30)
    if invocation_logs.status_code==200:
        files=[item["name"] for item in invocation_logs.json() if item["name"].endswith(".log")]
        if files:
            selected=sorted(files)[-1]
            detail=requests.get(
                "https://"+values["functionAppName"]+".scm.azurewebsites.net/api/vfs/LogFiles/Application/Functions/Function/ready_manifest/"+selected,
                headers={"Authorization":"Bearer "+token},allow_redirects=False,timeout=30)
            if detail.status_code==200:
                safe=[line for line in detail.text.splitlines()
                      if not re.search(r"AccountKey=|******",line,re.I)]
                result["hostInspection"]["readyManifestLog"]=[line[-1000:] for line in safe[-8:]]
                result["hostInspection"]["readyManifestLogTruncated"]=True
from azure.core.exceptions import AzureError
try:
    with ManagedIdentityCredential(client_id=values["uploaderIdentity"]["clientId"]) as credential:
        token=credential.get_token("api://"+values["apiClientId"]+"/.default").token
        probe=requests.get("https://"+values["functionAppName"]+".azurewebsites.net/api/faxes/00000000-0000-0000-0000-000000000000",
                           headers={"Authorization":"Bearer "+token},allow_redirects=False,timeout=30)
        result["hostInspection"]["apiStatus"]=probe.status_code
        if probe.headers.get("Content-Type","").startswith("application/json"):
            error=probe.json().get("error")
            if isinstance(error,str) and re.fullmatch(r"[a-z_]{1,80}",error):
                result["hostInspection"]["apiErrorCode"]=error
except (AzureError,requests.RequestException) as error:
    result["hostInspection"]["apiProbeErrorType"]=type(error).__name__
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError
result["pendingJobs"]=[]
with ManagedIdentityCredential(client_id=values["authorIdentity"]["clientId"]) as credential:
    with BlobServiceClient(values["dataStorageAccountUrl"],credential=credential) as blobs:
        for state_path in (root/"live-evidence").glob("**/run-state.json"):
            state=json.loads(state_path.read_text())
            for case,record in state.get("cases",{}).items():
                if record.get("status") not in {"submitting","inconclusive"}:
                    continue
                try:
                    job=json.loads(blobs.get_blob_client("jobs",record["faxId"]+".json").download_blob().readall())
                    result["pendingJobs"].append({
                        "case":case,
                        "status":job["status"],
                        "reason":job.get("reason"),
                        "originalReady":job.get("originalReady"),
                        "orchestrationIntentAt":job.get("orchestrationIntentAt"),
                        "orchestrationStarted":job.get("orchestrationStarted"),
                    })
                except ResourceNotFoundError:
                    reasons=[]
                    for receipt in blobs.get_container_client("jobs").list_blobs(name_starts_with="_admission-rejections/"+record["faxId"]+"/"):
                        data=json.loads(blobs.get_blob_client("jobs",receipt.name).download_blob().readall())
                        reasons.append(data["reason"])
                    result["pendingJobs"].append({"case":case,"status":"not_created","admissionRejections":reasons})
path=root/("development-state"+suffix+".json")
if path.exists():
    data=json.loads(path.read_text())
    result["developmentOperations"]={"postAttempts":data.get("postAttempts"),"states":{s:sum(v["status"]==s for v in data.get("operations",{}).values()) for s in ("succeeded","submitting","running","failed")}}
    result["actualExtractorDocTypes"]=sorted({d.get("docType","") for k,v in data.get("operations",{}).items() if k.endswith("-extract") for d in v.get("result",{}).get("analyzeResult",{}).get("documents",[])})
path=root/"promoted-model-settings.json"
if path.exists():
    data=json.loads(path.read_text())
    result["metadataDocTypes"]={k:v["resultDocType"] for k,v in data["modelSettings"]["documentTypes"].items()}
for path in Path("/opt/fax-test/logs").glob("*.log"):
    text=path.read_text(errors="replace")
    for line in text.splitlines():
        if line.startswith('{"event": "runner_failure"'):
            record=json.loads(line)
            result["diagnostics"][path.name]={k:record.get(k) for k in ("errorType","frames","httpStatus")}
    frames=re.findall(r'File "([^"]+)", line (\d+)',text)[-3:]
    kinds=re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):",text)[-1:]
    statuses=re.findall(r"(?:DI HTTP|HTTP Error|with HTTP) (\d{3})",text)[-3:]
    if frames or kinds or statuses:
        result["diagnostics"][path.name]={"frames":frames,"exceptionTypes":kinds,"httpStatuses":statuses}
if __INCLUDE_LOGS__:
    result={k:v for k,v in result.items() if k in {"hostInspection","pendingJobs"}}
    result.get("hostInspection",{}).pop("logFiles",None)
    result.get("hostInspection",{}).pop("rootFiles",None)
if __FIELD_DETAILS__:
    result={"live":[run for run in result.get("live",[]) if run["failures"]][-1:]}
encoded=json.dumps(result,separators=(",",":"))
if len(encoded)>3000:
    result["training"]={k:v for k,v in result.get("training",{}).items() if k!="operations"}
    if "development" in result:
        result["development"]={k:v for k,v in result["development"].items() if k!="cases"}
    encoded=json.dumps(result,separators=(",",":"))
if len(encoded)>3000:
    result={k:v for k,v in result.items() if k in {"deployment","live","pendingJobs","diagnostics"}}
    for run in result.get("live",[]):
        for failure in run.get("failures",[]):
            failure.pop("fieldMismatches",None)
            failure["failedChecks"]=failure.get("failedChecks",[])[:8]
    encoded=json.dumps(result,separators=(",",":"))
if len(encoded)>3500:
    raise RuntimeError("Summary exceeds safe RunCommand output budget; request narrower evidence")
print(encoded)
PY
'@
$script = $script.Replace('__INCLUDE_LOGS__', $(if ($IncludeHostLogs) { 'True' } else { 'False' }))
$script = $script.Replace('__FIELD_DETAILS__', $(if ($FieldDetails) { 'True' } else { 'False' }))
$body = @{location='westus2';properties=@{source=@{script=$script.Replace("`r`n","`n")};asyncExecution=$false;timeoutInSeconds=120;treatFailureAsDeploymentFailure=$true}} | ConvertTo-Json -Depth 8
$token = az account get-access-token --subscription $subscription --resource 'https://management.azure.com/' --query accessToken -o tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'ARM authentication failed.' }
$headers = @{Authorization="Bearer $token"}
$commandName = "fax-evidence-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$uri = "https://management.azure.com$vmId/runCommands/${commandName}?api-version=2024-11-01"
try {
    $null = Invoke-RestMethod -Uri $uri -Method Put -Headers $headers -Body $body -ContentType 'application/json' -MaximumRedirection 0
    for ($i=0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 5
        $command = Invoke-RestMethod -Uri "$uri&`$expand=instanceView" -Headers $headers -MaximumRedirection 0
        $view = $command.properties.instanceView
        if ($view.executionState -eq 'Succeeded' -and $view.exitCode -eq 0) {
            $summary = $view.output | ConvertFrom-Json
            $summary | ConvertTo-Json -Depth 15 | Set-Content $OutputFile -Encoding utf8NoBOM
            $summary | ConvertTo-Json -Depth 15
            return
        }
        if ($view.executionState -in @('Failed','TimedOut','Canceled')) { throw 'Private evidence summary failed.' }
    }
    throw 'Private evidence retrieval timed out.'
} finally { $token=$null; $headers.Clear() }
