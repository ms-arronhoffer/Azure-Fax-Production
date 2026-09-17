#requires -Version 7.2
[CmdletBinding()]
param([switch]$DeploymentApproved)
$ErrorActionPreference='Stop'
if (-not $DeploymentApproved) { throw 'Approved scoped diagnostics required.' }
$outputs=Get-Content (Join-Path $PSScriptRoot '..\artifacts\private-test.outputs.json') -Raw | ConvertFrom-Json -AsHashtable
$vmId=$outputs.runnerResourceId.value
$script=@'
#!/bin/bash
set -euo pipefail
/opt/fax-test/source/Azure-Fax-Production/.venv/bin/python - <<'PY'
import json,base64
from pathlib import Path
from azure.identity import ManagedIdentityCredential
import requests
root=Path("/opt/fax-test/source/Azure-Fax-Production")
values={k:v["value"] for k,v in json.loads((root/"artifacts/private-test.outputs.json").read_text()).items()}
config=json.loads((root/"artifacts/variants/happy/runtime-config.json").read_text())
url=values["runtimeDocumentIntelligenceEndpoint"].rstrip("/")+"/documentintelligence/documentClassifiers/"+config["classifier"]["id"]+"?api-version=2024-11-30"
code="""
import os,json,urllib.request,urllib.parse,urllib.error,base64
endpoint=os.environ['IDENTITY_ENDPOINT']
headers={'X-IDENTITY-HEADER':os.environ['IDENTITY_HEADER']}
request=urllib.request.Request(endpoint+'?api-version=2019-08-01&resource='+urllib.parse.quote('https://cognitiveservices.azure.com/',safe=''),headers=headers)
token=json.load(urllib.request.urlopen(request,timeout=30))['access_token']
payload=token.split('.')[1]
claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
result={k:claims.get(k) for k in ('oid','tid','aud')}
request=urllib.request.Request(URL,headers={'Authorization':'Bearer '+token})
try:
    response=urllib.request.urlopen(request,timeout=30)
    result['status']=response.status
except urllib.error.HTTPError as error:
    result['status']=error.code
    body=json.loads(error.read())
    detail=body.get('error',body)
    result['code']=detail.get('code',detail.get('statusCode'))
    message=str(detail.get('message',''))
    if not any(part in message.lower() for part in ('bearer ','sig=','accountkey=')):
        result['message']=message[:1000]
print(json.dumps(result))
""".replace("URL",repr(url))
with ManagedIdentityCredential(client_id=values["deployerIdentity"]["clientId"]) as credential:
    token=credential.get_token("https://management.azure.com/.default").token
    command="python3 -c \"import base64;exec(base64.b64decode('"+base64.b64encode(code.encode()).decode()+"'))\""
    response=requests.post("https://"+values["functionAppName"]+".scm.azurewebsites.net/api/command",
      headers={"Authorization":"Bearer "+token},json={"command":command,"dir":"/tmp"},
      allow_redirects=False,timeout=90)
    if response.status_code!=200:
        print(json.dumps({"scmStatus":response.status_code}))
    else:
        result=response.json()
        if result.get("ExitCode")==0:
            print(result["Output"])
        else:
            print(json.dumps({"exitCode":result.get("ExitCode"),"error":result.get("Error","")[-500:]}))
PY
'@
$token=az account get-access-token --subscription 79398e05-f318-4010-a508-d913c3763e39 --resource 'https://management.azure.com/' --query accessToken -o tsv --only-show-errors
$headers=@{Authorization="Bearer $token"}
$name="fax-identity-probe-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$uri="https://management.azure.com$vmId/runCommands/${name}?api-version=2024-11-01"
$body=@{location='westus2';properties=@{source=@{script=$script.Replace("`r`n","`n")};timeoutInSeconds=180;asyncExecution=$false}}|ConvertTo-Json -Depth 8
try {
    $null=Invoke-RestMethod -Uri $uri -Method Put -Headers $headers -Body $body -ContentType 'application/json'
    for($i=0;$i -lt 30;$i++){
        Start-Sleep -Seconds 5
        $data=Invoke-RestMethod -Uri "$uri&`$expand=instanceView" -Headers $headers
        $view=$data.properties.instanceView
        if($view.executionState -eq 'Succeeded') { Write-Output $view.output; return }
        if($view.executionState -in @('Failed','TimedOut','Canceled')) { throw 'Runtime identity diagnostic failed.' }
    }
    throw 'Runtime identity diagnostic timed out.'
} finally {$token=$null;$headers.Clear()}
