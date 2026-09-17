# Deployment guide

## Deployed synthetic reference environment

The standalone path is `infra\private-test.bicep`. The original
`infra\main.bicep` remains reusable with existing network/monitor inputs.
The isolated reference environment was deployed and tested with explicit approval.
The commands remain gated by `-DeploymentApproved` for reuse. Deployment of a new
environment still requires the operator's own approval, cost review, directory
authority and validated `.azure/deployment-plan.md`.

Fixed script context: West US 2; subscription
`79398e05-f318-4010-a508-d913c3763e39`; tenant
`16b3c013-d300-468d-ac64-7eda0820b6d3`; runtime
`rg-fax-e2e-westus2`; authoring `rg-fax-authoring-e2e-westus2`.
The initial cumulative test spend stop-and-ask threshold is **USD100**.
Review the estimate, egress exceptions and exact identities in
[infrastructure.md](infrastructure.md).

Verified reference resources include Function App
`func-faxe2e-qaund2hoqspjs-r2`, runtime Document Intelligence
`di-faxe2e-qaund2hoqspjs`, private runner `vm-faxe2e-runner`, and the
`ready-blob-created` Event Grid subscription. The Function permits public ingress
only from the `AzureEventGrid` service tag; EasyAuth excludes only the Functions
host Event Grid webhook (both path casings). User API and SCM paths remain
default-deny/private. Event Grid retries 30 times for 1,440 minutes and dead-letters
to the private `event-grid-deadletter` container.

### Stage A — local build and read-only gates

From `Azure-Fax-Production`:

```powershell
.\scripts\Deploy-PrivateTest.ps1 -Mode Build
.\scripts\Deploy-PrivateTest.ps1 -Mode Preflight
Copy-Item .\infra\private-test.example.bicepparam .\infra\private-test.local.bicepparam
```

Preflight queries only the approved subscription: tenant, registered providers,
D2s_v7 restrictions/family/regional vCPU quota, and App Service regional usage.
It never registers a provider or substitutes a VM SKU. In this subscription the
regional usage API returned only Dedicated-plan tiers, **not EP1**, so the
initial automated preflight correctly **stopped**. If that remains true, supply
fresh operator-read EP1 evidence using `-Ep1QuotaEvidencePath`; see
`infra\ep1-quota.example.json`. Evidence must identify this tenant/subscription,
West US 2, non-zonal EP1, source and UTC timestamp less than 24 hours old.
Do not reinterpret Premium v3/Compute quotas as EP1 quota. Planning evidence
was EP1 0/10 and EP1_AZ 0; no zonal deployment is requested.

Fill the local parameter file with a real existing approved API client ID, a
public SSH key only, monitored alert email, and the bounded instance count.
No PEM/private key should be created in or synchronized with this tree.
Parameter examples intentionally cannot pass an online deployment gate.

```powershell
.\scripts\Deploy-PrivateTest.ps1 -Mode Validate -ParameterFile .\infra\private-test.local.bicepparam
.\scripts\Deploy-PrivateTest.ps1 -Mode WhatIf -ParameterFile .\infra\private-test.local.bicepparam
```

ARM validation/what-if are read-only analyses, not deployment. What-if prints
resource IDs/change types only, avoiding app-setting values. If an isolated API
registration does not exist, stop before this stage until its creation is
separately approved. Compilation still works with example placeholders.

### Stage B — only after explicit cloud/directory approval

The following are **operator commands, not executed implementation steps**:

```powershell
# Omit creation and retain the existing apiClientId for a reusable enterprise deployment.
.\scripts\Initialize-TestIdentity.ps1 -Mode Create -DeploymentApproved
# Copy the emitted apiClientId into private-test.local.bicepparam, then rerun Validate/WhatIf.
.\scripts\Deploy-PrivateTest.ps1 -Mode Provision `
  -ParameterFile .\infra\private-test.local.bicepparam `
  -Ep1QuotaEvidencePath .\artifacts\ep1-quota.json -DeploymentApproved
.\scripts\Initialize-TestIdentity.ps1 -Mode Assign -DeploymentApproved
```

Creation makes a single-tenant v2-token API with `Fax.Submit`/`Fax.Review`
application roles, its assignment-required enterprise application, and an
isolated credentialless client registration. No API/client secret is generated.
Default display names are `fax-e2e-api` and `fax-e2e-client`; exact-name
collisions stop creation. The coordinator's read-only implementation checkpoint
found neither target resource group nor an exact `fax-e2e-api` registration.
That is not a reservation: repeat inventory before an approved deployment.
Directory read access does **not** prove directory create/assignment permission.
The runner uses the deployed UAMIs, not that placeholder client registration.
The assignment stage maps the uploader/reviewer UAMIs to **different** roles.
These isolated test roles are application roles. A reviewer-MI correction tests
service-principal authorization and revision mechanics; it does **not** prove
that an actual human reviewed a fax, even when the e2e protocol is named
`human-corrected-release`. Actual human acceptance requires a separately approved
delegated client, assigned human reviewer role/object ID, private connectivity
and a genuine delegated token. The MI-only test registration does not configure
that human access path. Never substitute an MI token or forged principal header
and label the result human-review proof.
The standalone wrapper accepts optional `humanReviewClientApplicationIds` and
`humanReviewPrincipalObjectIds`, both empty by default. Supply both only after
explicit authorization; they extend EasyAuth allowlists, **not** directory
consent or role assignments. Human reviewers still need the exact
case-sensitive `Fax.Review` role (`Fax.Admin` is not a substitute), a
User-enabled role definition/delegated permission flow on the API, and the
approved delegated client. Use a suitably configured existing API or obtain
separate approval for those directory changes; the MI-only registration helper
does not silently enable them.

The deployed app's role-protected
`GET /api/faxes/{faxId}/review/original` endpoint is the intended original-view
path: it uses the Function's storage identity, returns original bytes privately,
and avoids public SAS or granting reviewers broad Blob Reader. Validate this
endpoint with the actual authorized human before claiming human inspection.
Never equate a blob reference in review metadata with access to its contents.
Partial Graph failures preserve created IDs; investigate rather than blindly
recreating. Missing directory rights/consent is an explicit blocker, never a
reason for anonymous access.

Infrastructure admission remains disabled. After RBAC propagation, outputs in
`artifacts\private-test.outputs.json` contain only safe IDs/endpoints, including
runner identities, runtime/authoring DI, storage and all 11 private endpoint IDs.
Do not export Function app settings or storage keys as deployment evidence.

### Stage C — protected private-runner bootstrap

**Integration correction:** after training `prepare`, `build` and `promote`,
run the private runner's `Qualify` phase. It evaluates the six development faxes
against actual promoted models and calls the configuration generator with the
resulting evidence. It cannot enable from an invented calibration reference.

```powershell
.\scripts\Invoke-PrivateRunner.ps1 -Phase Qualify -Variant baseline -DeploymentApproved
.\scripts\Invoke-PrivateRunner.ps1 -Phase DeployCode -Variant baseline -DeploymentApproved
```

For each other approved variant, `Qualify` reuses validated development evidence
for the same models/corpus and generates the variant's configuration before
DeployCode. Runtime configuration hashes use sorted compact canonical JSON,
matching the application; whitespace changes do not change that hash.

```powershell
.\scripts\New-RunnerSource.ps1
.\scripts\Invoke-PrivateRunner.ps1 -Phase Bootstrap -DeploymentApproved
.\scripts\Invoke-PrivateRunner.ps1 -Phase CheckNetwork -DeploymentApproved
```

The bounded archive includes source under app/e2e/scripts only, **no `.pem` files,
venvs, installed dependencies, local settings, local secrets or generated fax
documents**. Review the source for other secrets before transfer; extension and
private-key-marker checks are not a general secret scanner. The source archive
is capped at 512 KiB compressed/20 MiB extracted; it crosses ARM only as managed
RunCommand **protected parameters**, not inline visible script/CLI arguments or a
public blob. ARM tokens remain in process memory. RunCommand stdout contains
only fixed status text, not source, credentials or document data.
Named Linux RunCommand parameters become environment variables. The base64 source
is split into 48-KiB chunks to stay below Linux per-variable and total environment
limits; a single multi-megabyte variable is not a supported bootstrap design.

Bootstrap installs Debian apt/Python dependencies on the private Linux x64 VM.
Its project is `/opt/fax-test/source/Azure-Fax-Production`; private logs are
`/opt/fax-test/logs`, mode restricted by `umask 077`. It refuses to overlay
existing source so retries cannot accidentally mix releases. Investigate a
partial bootstrap and approve explicit cleanup/replacement rather than retrying
blindly. Bootstrap is not code deployment.

The runtime package is built **on Linux Python 3.11** after dependencies are
installed. `certifi` CA `.pem` bundles in Linux-installed runtime dependencies
are legitimate trust material and must **not** be deleted. They are excluded
from source synchronization, not stripped from the private deployment ZIP.
Never sync that dependency-bearing ZIP back into a source export.

### Stage D — model qualification and private release

Stage a **nonsecret** JSON bundle using `Configure`. Its only allowed top-level
keys are `training-config`, `runtime-config` and `live-config`; each value is the
corresponding configuration object, not a filename. Training configuration follows
`e2e/training.example.json`; live configuration follows `e2e/runner.example.json`.
Do not put keys, tokens, SAS URLs or real patient data in the bundle.
Configuration is transferred privately, capped at 64 KiB, and written only under
the runner's `artifacts/` directory. `Configure` pins the live configuration hash
to the exact staged runtime-config bytes; `DeployCode` records that hash.
Use `-Variant <name>` consistently for Configure/DeployCode/Live. `baseline`
retains configuration in `artifacts/`; named variants use
`artifacts/variants/<name>/`. Training configuration/corpus/journal remain shared.
Route-none, route-multiple, HL7 NotApplicable and HL7-error expectations can
require incompatible runtime policies. Give each reviewed policy a distinct
variant and select only its compatible cases; do not claim all 22 pass under one
configuration. Calibrated model metadata can be reused without retraining.

Use `authorIdentity.clientId` for training and live `observerClientId`,
`uploaderIdentity.clientId` for live `runnerClientId` and `deniedClientId`, and
`reviewerIdentity.clientId` for live `reviewerClientId`. Populate resource IDs,
endpoints from deployment outputs; `apiAudience` is `api://<apiClientId>` and
`apiUrl` is `https://<functionAppName>.azurewebsites.net` **without `/api`**
(the e2e client adds that route prefix). Keep uploader access limited
to incoming/ready; observer output reads use the separate author identity.
Use unique versioned authoring/runtime model IDs and training prefixes. Do not
claim that example configuration or a guessed calibration ID is qualified.
The trusted integration source labels are `clinic-a` and `clinic-b`. They describe
configured source policy, not cryptographic evidence of a particular blob writer:
caller-supplied Blob metadata and manifest fields cannot establish actor
provenance. Upload `incoming/{faxId}/original` first and publish
`ready/{faxId}.json` last; allow only the configured integration identities to
write those containers.
Set `directUpload.trustedUploader` to
`16b3c013-d300-468d-ac64-7eda0820b6d3:<uploaderIdentity.principalId>`: this uses the
uploader's **object ID**, not its application/client ID. Enable directUpload and
include **both** clinic labels in `directUpload.allowedSources` and
`sources["<tenant>:<uploader-object-id>"]`. Each clinic needs explicit exam/order/rx
entries in `hl7Profiles` (a reviewed NotApplicable policy is an explicit entry).
The manifest is exactly
`{schemaVersion:1,faxId,source,mime,original:{container:"incoming",name:"<faxId>/original"},sha256:"<lowercase-64-hex>"}`;
do not add identity or other fields.

For a fresh runner/source release, use the tested order: Build/Bootstrap,
`SyncSource`, model `Train` stages, `Configure`, `DeployCode`, explicit trigger
synchronization and binding verification, `ResetLive`, then a bounded `Live`
smoke. `DeployCode` verifies that `ready_manifest` exposes the
`eventGridTrigger` and `durableClient` bindings. Disable admission after testing.
Do not omit `SyncSource`: stale Function metadata previously left an obsolete
trigger indexed after package upload.

Run training in separate bounded stages. `prepare` generates the five-per-class
synthetic training corpus and actual OCR/label artifacts; inspect those private
artifacts before approving `build`. `promote` records the actual copied runtime
model metadata in `artifacts/training-state.json`. Produce the reviewed schema-2
policy using `artifacts/promoted-model-settings.json`, exported with GET-verified
IDs/docTypes and deliberately disabled activation, rather than mock model IDs.
Produce the reviewed
runtime configuration from those actual classifier labels/extractor docTypes,
source/routing policies and measured calibration, then stage it together with
the matching live configuration. Training does **not** invent a passing runtime
policy or calibration certificate.
For a generated rx HL7 profile, explicitly configure `doseUnits` (for example
`TESTUNIT` for non-actionable synthetic fixtures). `Dose` must be
a numeric value or plain decimal string; zero is valid, while `10 mg` must be
corrected or routed to an explicitly approved NotApplicable policy. The runtime
does not infer missing units from the shared field set.
Keep fixtures conspicuously non-actionable: numeric Dose `0`, fictional
medication names, Route `SIMULATION ONLY` and Frequency `NEVER ADMINISTER`.

The `DeployCode` phase verifies policy using both `validate_config` and
`contracts.validate_v2` (including schema version and ordered fields), and model
schemas using author MI against runtime DI, then builds the dependency-
bearing ZIP, checks private DNS, authenticates SCM with the **deployer MI's Entra
ARM token**, disables admission, uploads privately and waits for SCM completion:

```powershell
.\scripts\Invoke-PrivateRunner.ps1 -Phase Configure `
  -ConfigurationBundle .\artifacts\training-bundle.json -DeploymentApproved
.\scripts\Invoke-PrivateRunner.ps1 -Phase Train -TrainingStage preflight -DeploymentApproved
.\scripts\Invoke-PrivateRunner.ps1 -Phase Train -TrainingStage prepare -DeploymentApproved
# Inspect actual OCR/labels on the private runner before continuing.
.\scripts\Invoke-PrivateRunner.ps1 -Phase Train -TrainingStage build -DeploymentApproved
.\scripts\Invoke-PrivateRunner.ps1 -Phase Train -TrainingStage promote -DeploymentApproved
.\scripts\Invoke-PrivateRunner.ps1 -Phase Configure `
  -Variant baseline -ConfigurationBundle .\artifacts\qualified-runtime-and-live-bundle.json -DeploymentApproved
.\scripts\Invoke-PrivateRunner.ps1 -Phase DeployCode -Variant baseline -DeploymentApproved
# Example compatible subset, NOT full acceptance coverage:
.\scripts\Invoke-PrivateRunner.ps1 -Phase Live -Variant baseline `
  -Cases exam-pdf-body -DeploymentApproved
# After inspecting that smoke result and costs, select additional compatible work:
.\scripts\Invoke-PrivateRunner.ps1 -Phase Live -Variant baseline `
  -Cases exam-pdf-body,missing-required `
  -Protocols duplicate,conflict,admission-rejection,human-corrected-release,denied-review,concurrent-review `
  -DeploymentApproved
# Configure/deploy separately reviewed variants, then select their matching cases:
# e.g. -Variant routes-none -Cases routes-none (without unrelated protocols).
# Emergency/final admission stop (does not deallocate paid infrastructure):
.\scripts\Invoke-PrivateRunner.ps1 -Phase Disable -DeploymentApproved
```

`Live` checks the staged/deployed configuration hashes, identities, endpoints and
live DI schemas, temporarily enables processing, runs the explicitly selected
compatible cases and requested protocols (duplicate, conflict, admission-rejection,
human-corrected-release, denied-review and concurrent-review),
and disables processing in `finally`. Each phase has a 45-minute child-process
budget inside the one-hour RunCommand limit; incomplete runs fail, not pass.
The initial training manifest is capped at 50 stored references/20 unique layout
pages and the persistent training journal at 50 total POST attempts across all stages. There is no
automatic retrain loop. A client timeout does **not** cancel an already submitted
DI operation; inspect the journal/service state and current costs before retrying.
The main live suite is capped at 22 cases/27 readable pages; the six fixed
protocol tests are additional. Enlarging these limits needs a new cost review.
Extractor requests cap `maxTrainingHours` at 0.5 each; classifier builds do not
have that same request field. Do not advertise a universal training billing cap.
Calls within one variant share `live-evidence/<variant>/run-state.json` so protocols
can locate their prerequisite cases. The wrapper totals attempts across all
variant journals and preserves a global 30-attempt cap; do not clear journals to
silently obtain a new allowance. It snapshots
each suite's JSON/JUnit summary into its own subdirectory before a later call
can overwrite the shared summary. A VM/ARM outage or forced process kill can
prevent the cleanup write: inspect admission and run `Disable` before retrying.
Mutating phases use a private VM lock; the emergency `Disable` bypasses that lock.
Do not run a new Live command concurrently with an emergency shutdown.
Use `docs/testing.md` to build the case-to-configuration coverage matrix. A
successful subset is not all-case acceptance; retain every variant's exact
configuration hash, case results and protocol evidence. Pending/blocked variants
and absent real-human acceptance must remain explicitly unverified.

Pass actual cumulative spend through `-SpendToDateUsd` on subsequent runner and
provisioning calls. Reconcile usage before every training/build/live stage.
The USD100 gate is a stop-and-ask policy, **not** a real-time meter, automatic
budget resource, or guarantee that delayed usage cannot exceed the threshold.
Stopping admission does not stop EP1/NAT/private-endpoint charges.

No public SCM/basic authentication/storage bootstrap is enabled. If Entra SCM
returns 401/403, investigate Website Contributor and identity propagation.
Do not fall back to publishing credentials. Positive acceptance must include
original-then-ready upload, Blob listener/Durable startup, private DNS for both
runtime and SCM, model promotion, expected fields/routing, correct reviewer
release and wrong-role denial. Validate AMPLS's actual ingestion/query hostnames
from the runner and Function worker, and test alert delivery without PHI.
Local compile/package checks or a successful SCM ZIP upload alone do not prove
any of these acceptance conditions.

### Stage E — ledger, cost checkpoints and separately approved shutdown

`Write-TestLedger.ps1` appends safe operational records to
`artifacts\test-run-ledger.jsonl`. Approved provisioning records a UTC start
timestamp immediately before ARM create and a success timestamp after it.
RunCommand phases record start/success/failure and the explicit training stage.
Records contain only fixed scope, phase, deployment name, timestamps and
operator-reported cumulative spend—never tokens, keys, configuration contents or
fax content. A start without success means **inspect partial resources**, not
that provisioning was rolled back. This local ledger is not immutable audit
storage or an independent Azure billing meter.

Record reconciled spend before subsequent work:

```powershell
# Enter reconciled actual spend, NOT the planning estimate; local record only.
$ObservedSpendUsd = [decimal](Read-Host 'Actual cumulative synthetic-test spend in USD')
.\scripts\Write-TestLedger.ps1 -Event CostCheckpoint -SpendToDateUsd $ObservedSpendUsd
```

Use the first approved provisioning start to track the intended maximum
72-hour test window. Stop and ask before exceeding the window or USD100.
Preserve only safe acceptance summaries before cleanup; never export private
keys, dependency bundles, raw credentials or real documents.

The following are **instructions only**, requiring explicit shutdown/cleanup
approval. They have not been executed. First disable admission while the runner
still operates; inspect outstanding test work and wait for safe completion.
Deallocate only the VM ID from this deployment:

```powershell
$CleanupApproved = $false # Set true only after separate operator approval.
if (-not $CleanupApproved) { throw 'Cleanup approval required.' }
$subscription = '79398e05-f318-4010-a508-d913c3763e39'
$tenant = az account show --subscription $subscription --query tenantId -o tsv
if ($LASTEXITCODE -ne 0 -or $tenant -ne '16b3c013-d300-468d-ac64-7eda0820b6d3') { throw 'Wrong context.' }
.\scripts\Invoke-PrivateRunner.ps1 -Phase Disable -DeploymentApproved
$outputs = Get-Content .\artifacts\private-test.outputs.json -Raw | ConvertFrom-Json
$runner = $outputs.runnerResourceId.value
if ($runner -notmatch "^/subscriptions/$subscription/resourceGroups/rg-fax-e2e-westus2/providers/Microsoft.Compute/virtualMachines/[a-z0-9-]+$") { throw 'Unexpected runner scope.' }
.\scripts\Write-TestLedger.ps1 -Event CleanupStarted
az vm deallocate --ids $runner --subscription $subscription --only-show-errors
if ($LASTEXITCODE -ne 0) { throw 'Deallocation failed.' }
$state = az vm get-instance-view --ids $runner --subscription $subscription `
  --query "instanceView.statuses[?code=='PowerState/deallocated'].code | [0]" -o tsv
if ($LASTEXITCODE -ne 0 -or $state -ne 'PowerState/deallocated') { throw 'Deallocation is not verified.' }
.\scripts\Write-TestLedger.ps1 -Event RunnerDeallocated
```

**VM deallocation stops VM compute only**. OS disks, EP1, NAT, public egress IP,
private endpoints and stored/retained data can keep accruing charges. An OS
shutdown without Azure deallocation can also leave VM compute billed.
To remove the entire disposable deployment, obtain explicit approval to delete
both groups, inventory them for unrelated resources/locks, and use interactive
confirmation—do not blindly delete by a name pattern:

```powershell
# Requires separately approved deletion, not merely deallocation approval.
$DeleteGroupsApproved = $false
if (-not $DeleteGroupsApproved) { throw 'Separate group deletion approval required.' }
az resource list --subscription $subscription --resource-group rg-fax-e2e-westus2 `
  --query "[].{name:name,type:type,id:id}" -o table
az resource list --subscription $subscription --resource-group rg-fax-authoring-e2e-westus2 `
  --query "[].{name:name,type:type,id:id}" -o table
az group delete --subscription $subscription --name rg-fax-e2e-westus2
if ($LASTEXITCODE -ne 0) { throw 'Runtime group deletion failed.' }
az group delete --subscription $subscription --name rg-fax-authoring-e2e-westus2
if ($LASTEXITCODE -ne 0) { throw 'Authoring group deletion failed.' }
foreach ($group in @('rg-fax-e2e-westus2','rg-fax-authoring-e2e-westus2')) {
  $exists = az group exists --subscription $subscription --name $group -o tsv
  if ($LASTEXITCODE -ne 0 -or $exists -ne 'false') { throw 'Group deletion not verified.' }
}
.\scripts\Write-TestLedger.ps1 -Event GroupsDeleted
```

Resource-group deletion is not directory cleanup. The directory owner must
verify the exact saved API/client object IDs in `test-api-identity.json`, remove
only this test's application-role assignments/registrations after separate
directory approval, and never delete a reused enterprise API. Reconcile delayed
costs, retained/soft-deleted resources, and the ledger after cleanup. No automatic
resource deletion or directory deletion is implemented.

The remainder of this guide covers the original existing-infrastructure path.

Deploy this package from an approved **private network-connected runner**.
It intentionally does not enable public endpoints to make deployment easier.
No Azure resource has been created merely by generating these files.

Read [infrastructure.md](infrastructure.md) for the exact parameter names,
script switches, resource outputs and platform-specific settings.

## 1. Assign owners and obtain prerequisites

Gather the following before running deployment commands:

| Item | Owner / evidence |
|---|---|
| Azure subscription and dedicated resource group | Platform owner and cost center |
| Region supporting the selected Functions/Storage/DI SKUs | Platform owner; capacity/quota check |
| Existing delegated Function integration subnet | Network owner; sufficient free addresses |
| Existing private endpoint subnet | Network owner; appropriate endpoint policies |
| Private DNS zones and resolver/forwarding | Network owner; private IP resolution tests |
| Private runner with ARM access and private SCM connectivity | Release engineering |
| Existing Power Platform VNet integration and supported connector | Power Platform administrator |
| Single-tenant API and connector registrations | Entra application administrator |
| Allowed client IDs, app roles and source/caller mapping | Business/security owner |
| Model IDs and exact mandatory field schemas | Document-model owner |
| Approved routing predicates/destination convention | Business owner |
| Monitoring private-link configuration and alert recipients | Operations owner |
| Data retention, review process and recovery objectives | Data owner / compliance |

Use a distinct development/test resource group first. Production samples must
not be used as convenient test fixtures.

### Required tools on the deployment runner

- Current Azure CLI with Bicep support.
- Python **3.11**, matching the deployed runtime, and `pip`/`venv`.
- PowerShell 7 for the supplied scripts.
- Azure Functions Core Tools v4 when using local host checks or a supported
  publish workflow. The private ZIP deployment path is documented separately.
- Network access to approved Python package feeds for the build stage, or an
  internal mirror containing the pinned dependencies.

The production artifact must contain Linux-compatible dependencies. A Windows
virtual environment cannot simply be zipped and run on a Linux Function App.
Use the matching Linux x64 Python 3.11 build runner described in the
infrastructure guide. The supplied template disables remote build.

## 2. Configure Entra authentication before deployment

Follow [rbac.md](rbac.md) to create:

1. API single-tenant registration, audience and delegated scope.
2. `Fax.Submit`, `Fax.Review` and tightly assigned `Fax.Admin` application roles.
3. Assigned users/groups and approved client applications.
4. Connector OAuth client with the eventual connector redirect URI.

Bicep configures the Function's authentication policy using these supplied IDs;
it does not create or consent the Entra registrations.

Record both the IDs and their meaning. A service principal object ID is not
interchangeable with an application/client ID.

## 3. Prepare the private network

Confirm the existing integration subnet is delegated to the required App
Service provider, is in the deployment region and has capacity for scale and
deployment operations. Do not reuse the private endpoint subnet for delegated
Function integration.

Validate private DNS for:

- Function application and **SCM** site:
  `privatelink.azurewebsites.net`.
- Business and host Storage:
  `privatelink.blob.core.windows.net`,
  `privatelink.queue.core.windows.net`,
  `privatelink.table.core.windows.net`, and
  `privatelink.file.core.windows.net` where used.
- Document Intelligence:
  `privatelink.cognitiveservices.azure.com`.
- The configured Azure Monitor Private Link Scope and its required DNS zones.

Use the actual output hostnames after deployment:

```powershell
Resolve-DnsName "<function-app>.azurewebsites.net"
Resolve-DnsName "<function-app>.scm.azurewebsites.net"
Resolve-DnsName "<data-account>.blob.core.windows.net"
Resolve-DnsName "<data-account>.queue.core.windows.net"
Resolve-DnsName "<document-service>.cognitiveservices.azure.com"
Test-NetConnection "<function-app>.scm.azurewebsites.net" -Port 443
```

Run equivalent tests from the runner and verify the Power Platform connector's
path independently. Successful resolution on your laptop does not prove the
flow runs on that network.

Private data planes do not remove Azure control-plane/identity dependencies.
Firewalls must allow the approved ARM, Entra authentication, deployment,
package-feed and platform routes needed for provisioning/runtime. Document
those egress requirements with the network team rather than granting blanket
internet access to solve a single missing route.

## 4. Author and promote models

Follow [models.md](models.md) in the approved authoring environment:

1. Train the cover/body classifier.
2. Train or select each business-field extraction model.
3. Evaluate all expected fields and cover handling against held-out faxes.
4. Copy/promote approved models into the private runtime DI resource after it
   exists. Infrastructure can be deployed first with processing disabled.
5. Verify target model IDs using authenticated calls over the private endpoint.

Do not enable admission with example/nonexistent model IDs.

## 5. Create environment-specific parameter/configuration files

Copy the example Bicep parameter file in `infra` to a local environment file.
Keep the local copy out of source control where it contains tenant/network
identifiers your organization treats as sensitive.

Fill every required value from approved infrastructure, not guessed GUIDs:

- Location/naming/environment parameters.
- Existing subnet and private DNS resource IDs.
- Monitoring private-link IDs and alert settings.
- API application/client IDs and allowed OAuth clients.
- Deployment/runtime options documented by the template.

The exact required template inputs are `namePrefix`, `location`, `apiClientId`,
`allowedClientApplicationIds`, `integrationSubnetId`, `privateEndpointSubnetId`,
`privateDnsZoneIds`, `existingAmplsResourceId` and `actionGroupResourceId`.
The `privateDnsZoneIds` object requires `blob`, `queue`, `table`, `file`, `sites`
and `cognitiveServices` keys. AMPLS must already have its approved private
endpoint, DNS and PrivateOnly access modes. The action group must have actual
operational recipients.

```powershell
Copy-Item .\infra\main.example.bicepparam .\infra\main.local.bicepparam
# Edit all example IDs, region, names and approved runtime options before use.
.\scripts\Deploy.ps1 -Mode Build -ParameterFile .\infra\main.local.bicepparam
```

Prepare the application pipeline configuration according to [api.md](api.md):

- Real versioned classifier/model IDs.
- Supported cover and body label mappings.
- Mandatory expected fields with correct spelling/types.
- Authorized caller-to-source mapping.
- Bounded routing rules with all-match semantics.
- Processing limits compatible with service quota and business expectations.

Admission should remain disabled until configuration and models pass preflight.
Keep code, schema, rule and model versions together in a release record.

## 6. Authenticate and validate infrastructure

The commands below are for the operator to run; they are not executed by this
document. Verify the intended tenant/subscription before creating resources.

```powershell
az login --tenant "<tenant-id>"
az account set --subscription "<subscription-id>"
az account show --query "{Subscription:name,Id:id,Tenant:tenantId}" --output table
```

Create the dedicated group if your platform process has not already done so:

```powershell
az group create --name "<resource-group>" --location "<approved-region>"
```

Ask a subscription administrator to register missing resource providers:
Microsoft.Web, Microsoft.Storage, Microsoft.Network, Microsoft.CognitiveServices,
Microsoft.Insights and Microsoft.OperationalInsights. Do not grant a routine
application deployer subscription Owner just for provider registration.

Compile and inspect what-if, using your populated `.bicepparam` file:

```powershell
az bicep build --file .\infra\main.bicep
az deployment group what-if `
  --resource-group "<resource-group>" `
  --template-file .\infra\main.bicep `
  --parameters .\infra\main.local.bicepparam
```

Stop for unexpected deletions, replacements, broad role assignments or public
network changes. A successful Bicep build checks schema/syntax, not live quota,
private networking or business correctness.

## 7. Deploy infrastructure

Use the supplied [deployment helper](../scripts/Deploy.ps1), or an approved ARM
pipeline. The helper rejects example IDs, enforces admission-disabled
infrastructure deployments and suppresses sensitive property output:

```powershell
.\scripts\Deploy.ps1 -Mode Validate `
  -ParameterFile .\infra\main.local.bicepparam `
  -SubscriptionId "<subscription-guid>" -ResourceGroup "<resource-group>"
.\scripts\Deploy.ps1 -Mode WhatIf `
  -ParameterFile .\infra\main.local.bicepparam `
  -SubscriptionId "<subscription-guid>" -ResourceGroup "<resource-group>"
.\scripts\Deploy.ps1 -Mode Infrastructure -DeploymentApproved `
  -ParameterFile .\infra\main.local.bicepparam `
  -SubscriptionId "<subscription-guid>" -ResourceGroup "<resource-group>"
```

The equivalent underlying group deployment is:

```powershell
az deployment group create `
  --name "private-fax" `
  --resource-group "<resource-group>" `
  --template-file .\infra\main.bicep `
  --parameters .\infra\main.local.bicepparam
```

Check the native command exit code and the ARM deployment's provisioning state.
Do not continue to code deployment after a failed infrastructure operation.

Retain only nonsecret deployment outputs: Function app name, service endpoints,
resource IDs and runtime identity ID. Never print all Function app settings or
Storage keys to diagnose a failed mount.

Allow for RBAC propagation, then verify:

- Private endpoints are approved and DNS records exist.
- Runtime storage access works with the assigned identity.
- Host/Durable storage connections and content mount work.
- DI private endpoint and inference authorization work.
- EasyAuth returns 401 for an unauthenticated private request.
- Requests from outside the private network cannot reach the data planes.
- Monitoring is connected through the intended private-link scope.

## 8. Build, validate and deploy Python

Follow [api.md](api.md) for the exact local configuration preflight and tests.
Create an isolated Python 3.11 environment; do not reuse an unrelated workspace's
global packages.

Run dependency installation from the package's pinned requirements, then the
unit tests. Run the **live** model/schema check from the private runner with a
model-read-capable identity after target models exist.

The combined local check uses an explicitly selected interpreter:

```powershell
.\scripts\Validate-Package.ps1 -PythonExecutable "<full-path-to-prepared-python.exe>"
```

This invokes the packaged unit tests, Bicep compilation and editable diagram
structure checks. It does not provision resources or contact the model service.
On a Linux private runner, supply the prepared interpreter's absolute Linux
path instead.

Package using [Package.ps1](../scripts/Package.ps1) and the supported Linux build
path documented in [infrastructure.md](infrastructure.md). Ensure:

1. `host.json` and `function_app.py` are at the ZIP root, not inside an `app`
   subfolder.
2. The approved pipeline config is included.
3. Local settings, virtual environments, test documents and tokens are excluded.
4. The build process installs dependency wheels compatible with the deployed
   Linux/Python runtime.
5. A hash and release version are recorded for the artifact.
6. The release runner can reach the **private SCM endpoint**.

The supplied packager uses a **Linux x64 Python 3.11 runner with PowerShell 7.2+**.
It preinstalls the pinned requirements; remote Oryx build is disabled in Bicep.
It deliberately refuses Windows-built packages with incompatible binary wheels.

```powershell
# Run on the approved Linux build runner from the package root.
.\scripts\Package.ps1 `
  -ConfigPath .\app\pipeline\config.json `
  -PythonExecutable python3.11

# Run on an authorized private runner with the artifact and SHA256 sidecar.
.\scripts\Deploy.ps1 -Mode Code -DeploymentApproved `
  -SubscriptionId "<subscription-guid>" `
  -ResourceGroup "<resource-group>" `
  -FunctionAppName "<functionAppName-output>" `
  -PackagePath .\artifacts\fax-function.zip
```

Code mode checks the artifact SHA256 and private SCM DNS, disables admission,
authenticates to SCM with an in-memory Entra token, then polls deployment status.
It does not re-enable processing automatically.

Deploy code without enabling public SCM/basic authentication as a shortcut.
Verify function indexing/startup and dependency imports before opening
admission. Successful package deployment alone does not prove runtime startup.

## 9. Configure the Power Platform solution

Follow [power-platform.md](power-platform.md):

1. Create/approve the private custom connector and OAuth connection.
2. Build the upload/status flow and canvas-app screen.
3. Secure action inputs/outputs and apply DLP/run-history controls.
4. Assign submitter and reviewer identities.
5. Verify the flow's actual token principal and allowed source mapping.
6. Export/import through the organization's Power Platform solution pipeline.

Power Platform configuration is intentionally a tenant-bound deployment step;
the Azure template cannot grant Power Platform consent or create a user's
connection reference.

## 10. Enable and verify a controlled release

1. Confirm models/config/schema preflight passes.
2. Enable the documented processing/admission setting through your release
   process.
3. Submit a small controlled set of synthetic/sanitized faxes.
4. Execute the full [acceptance matrix](operations.md).
5. Verify the all-fields 80% gate before testing downstream consumption.
6. Verify no rejected fax appears in accepted primary/routed outputs.
7. Verify retry behavior, partial-publication handling and human-review access.
8. Confirm the alert recipient receives a controlled test alert.
9. Obtain business, security and operations approval.
10. Expand volume gradually, monitoring model quality, latency and backlog.

Disabling admission does not necessarily cancel already admitted Durable jobs.
Use the documented drain/cancel process for maintenance rather than assuming
an app setting atomically stops all work.

## 11. Rollback and recovery

- Keep the previous code artifact, configuration and model IDs.
- Disable new admission when investigating a release.
- Drain or explicitly manage in-flight jobs before changing incompatible code.
- Roll back the code/config pair, not just a model ID.
- Keep rejected drafts, original scores and reviewer audit intact.
- Preserve partial outputs and job state for investigation; downstream consumers
  must continue to honor completion state.
- Test recovery in nonproduction before performing destructive cleanup.

For a region outage, follow the separately approved RTO/RPO and DR plan. This
baseline is not a turnkey active-active regional failover system.

## Troubleshooting quick reference

| Symptom | Check first |
|---|---|
| 401 | Token issuer/audience/expiry, EasyAuth settings |
| 403 from business API | `roles` claim, client allowlist, caller/source ownership |
| 403 from Storage/DI | Identity role, correct scope, propagation, firewall/network |
| DNS resolves public address | Zone group, zone link, DNS forwarder/client network |
| App deploys but functions do not index | ZIP root, requirements/runtime, host logs |
| Host/content mount fails | Host account networking, file PE/DNS, share and platform settings |
| Upload received but no work progresses | Queue connection, host identity, queue/Durable diagnostics |
| Every extraction rejects | Actual schema/model fields and confidence, classifier labels, source mapping |
| Trained model not found | Wrong target endpoint/model ID, incomplete copy or wrong API version |
| Studio labels load but build fails | Service-to-training-storage identity/network, not just browser access |
| Artifacts exist but status failed | Partial publication; do not consume until recovery completes |
