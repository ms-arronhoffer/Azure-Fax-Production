# Private infrastructure and release operations

## Standalone West US 2 synthetic test wrapper

`infra\private-test.bicep` composes, rather than replaces, `main.bicep`.
`scripts\Deploy-PrivateTest.ps1` is pinned to subscription
`79398e05-f318-4010-a508-d913c3763e39`, tenant
`16b3c013-d300-468d-ac64-7eda0820b6d3`, and these resource groups:

| Boundary | Owned resources |
|---|---|
| `rg-fax-e2e-westus2` | Existing runtime modules; owned VNet, NAT gateway/egress public IP, private DNS zones/links, NSG, private Linux runner, four runner UAMIs, AMPLS and email action group |
| `rg-fax-authoring-e2e-westus2` | Separate S0 Document Intelligence with system MI; training storage/container and private endpoints |

The test uses **EP1, min 1/max 1** (max 2 explicitly selectable), **LRS**,
**Standard_D2s_v7** Linux x64 runner (2 vCPU/8 GiB), 64-GiB Standard SSD OS disk.
Debian 12 supplies Python 3.11 without unapproved PPAs. There is **no zonal HA
claim**. Preflight evidence supplied for planning was EP1 0/10, EP1_AZ 0, D2s_v7
unrestricted with family 0/100; requery before allocation.

The reference environment is live. Event Grid reaches the Functions host through
an App Service access restriction that allows only the `AzureEventGrid` service
tag; the default action is deny and SCM does not inherit that exception. EasyAuth
continues protecting application routes and excludes only
`/runtime/webhooks/EventGrid` and its lowercase equivalent for the Functions host
extension. The API, SCM, Storage and Document Intelligence data paths otherwise
use private endpoints. Failed Event Grid deliveries are retained in the private
`event-grid-deadletter` Blob container.

The runner NIC has **no public IP** and its NSG denies all inbound traffic,
including SSH from the VNet. The SSH parameter is a public key only, required by
Linux VM provisioning; do not create/sync a PEM key. Access is through authorized
ARM managed RunCommand. No Bastion, load balancer or inbound NAT rule is created.
NAT's public IP is outbound-only. **NAT is not a destination firewall**: the test
allows outbound connectivity through NAT and is not an exfiltration-proof system.
Production destination filtering requires a separately approved firewall/proxy.

Subnets are `10.84.0.0/26` (delegated integration), `10.84.1.0/24`
(private endpoints), and `10.84.2.0/27` (runner), in owned `10.84.0.0/16`.
No peering or shared-network changes occur. Default implicit outbound access is
disabled; runner and integration use explicit NAT. The private endpoint subnet
is sized for AMPLS's multi-address NIC as well as application endpoints.

There are **11 private endpoints**: the original eight, authoring DI, training
blob, and AMPLS. Ten private DNS zones are linked to the VNet: the six runtime
zones below plus `privatelink.monitor.azure.com`,
`privatelink.oms.opinsights.azure.com`, `privatelink.ods.opinsights.azure.com`,
and `privatelink.agentsvc.azure-automation.net`. AMPLS also uses the existing
linked `privatelink.blob.core.windows.net` zone. Its five-zone DNS group is
created **after** workspace/Application Insights scoped links. Both AMPLS access
modes are explicitly `PrivateOnly`; public telemetry ingestion/query remain off.
No document text, OCR, names, identifiers, request bodies, tokens or source
archives belong in telemetry/alert payloads.

### Test identity boundaries

| Identity | Permission |
|---|---|
| Runtime system MI | Runtime RBAC matrix below; no training access |
| Uploader UAMI | Blob Data Contributor on **incoming and ready only**; `Fax.Submit` app role |
| Reviewer UAMI | `Fax.Review` app role only; no storage/DI Azure RBAC |
| Deployer UAMI | Website Contributor (`de139f84-1756-47ae-9be6-808fbbe84772`) on **this Function App only** for private Entra SCM and app settings |
| Author UAMI | Cognitive Services User (`a97b65f3-24c7-4388-baec-2e87135dc908`) on the two test DI accounts for build/copy/promotion; training-container Blob Contributor; runtime **blob-service Reader** for synthetic result assertions |
| Authoring DI system MI | Training-container Blob Contributor, as documented for managed-identity training |

All runner UAMIs share one VM trust boundary: any VM administrator/RunCommand
operator can request any attached identity. They test identity authorization, not
hostile workload isolation. Never let untrusted code or real PHI onto this runner.
Optional `humanReviewClientApplicationIds` and `humanReviewPrincipalObjectIds`
extend the private EasyAuth allowlists for a separately approved delegated human
review path. Defaults remain MI-only; these inputs grant no app role, directory
consent, storage access or public ingress. The private role-protected original
endpoint is the original-view path; actual human review remains a separate
acceptance gate from MI authorization tests.
The author MI is privileged authoring/promotion access, not the inference-only
runtime role; revoke its runtime promotion grant after qualification if retaining
the environment.

Training storage deliberately uses **publicNetworkAccess Enabled + default Deny
+ zero IP/VNet allow rules + AzureServices trusted-service bypass**, with a
resource-instance rule for the authoring DI. It is not an unrestricted public
endpoint and not the same policy as runtime storage's public access Disabled.
Its trusted-service bypass is a documented network exception; identity RBAC is
still required. Clients use its private blob endpoint. No laptop-IP or Studio-IP
exception, SAS, shared key, or runtime storage bypass is added. See
[Microsoft's secured training guidance](https://learn.microsoft.com/azure/ai-services/document-intelligence/authentication/managed-identities-secured-access).

### Cost and egress boundaries

Planning retail inputs (USD, West US 2, subject to refresh): EP1
`0.16/vCPU-hour + 0.0114/GiB-hour × 3.5 = 0.1999/hour`, runner `0.132/hour`,
NAT `0.045/hour`, 11 private endpoints `11 × 0.01/hour`. A **72-hour one-instance
base is about USD35.06**, before OS disk, public IP, DNS, storage, bandwidth/NAT
processing, DI pages/training, monitoring, taxes or a second EP1 instance.
The permitted two-instance ceiling is about **USD49.45 for 72 hours** before those
extras; preflight conservatively uses that ceiling for its stop-and-ask gate.
This is an estimate, not a quote. Use the [Azure retail prices API](https://prices.azure.com/api/retail/prices)
and actual Cost Management charges before increasing scope. Stop and ask at
**USD100 cumulative test spend**, or sooner if projected cost would exceed it.
No budget alarm is an immediate hard spending cap; cost ingestion can lag.
Deleting/deallocating resources requires its own approved operator action.
Stopping the runner does not stop EP1/NAT/private-endpoint charges.

The initial runner deliberately uses the minimum five training samples per type,
not a thousand-page evaluation. Based on the current synthetic generator:

| Initial operation | Expected count before retries |
|---|---:|
| Stored one-page training PDFs | 50: 15 extractor + 15 type-classifier + 15 page/body + 5 page/cover |
| Separate development documents | 6 one-page documents; not automatically trained or calibrated |
| Main held-out suite | 20 readable documents / 27 pages, plus 2 malformed byte fixtures |
| Six separate protocol tests | 2 isolated review originals/pages; checksum-rejection uploads one additional original but should invoke no DI; duplicate/conflict replays should not rerun inference |
| Build submissions | 5: 2 classifiers + 3 extraction models |
| Promotion | 5 authorize-copy + 5 copy submissions; GET verification afterward |

The five-per-type corpus has 20 unique first-pass layout pages after SHA256
deduplication of the 50 stored references; preflight reports the actual unique
count and the wrapper refuses a larger value. Preparation plus builds/copy
therefore plans **35 DI POSTs** (20 layout + 5 builds + 10 copy/authorization).
The main held-out suite plans 47 classifier page-passes and at most 20 custom
pages; the two isolated review prerequisites add four classifier passes and at
most two custom pages. Across all compatible configuration variants and six
protocols, plan **51 classifier page-passes and at most 22 custom pages**.
There is no separately invoked Read operation in these runner stages.

At the verified S0 rates in [test-budget.md](test-budget.md), those first-pass
page charges are approximately `$0.20 + $0.153 + $0.66 = $1.013`.
**Assuming**, not guaranteeing, 2.5 total billed training hours adds `$7.50`,
giving an illustrative initial DI subtotal of **$8.513**, before retries,
independent calibration or extra runs. No free allowance is assumed.
Only extractor requests carry the 0.5-hour training limit; client timeouts do not
cancel service training, and classifier durations need observation. The
72-hour one-instance base plus that illustrative DI subtotal is about **$43.57**,
still excluding disk/IP/DNS/storage/monitoring/egress and other usage.
The initial 50-reference/20-unique-page corpus and persistent 50-POST journal cap fail closed;
expanding the corpus, clearing the journal or retraining needs a new cost review.

Required egress includes Entra login/metadata and certificate validation, ARM,
Microsoft Graph for the operator's directory setup, Azure VM agent endpoints,
Debian apt security/package mirrors, PyPI/files.pythonhosted.org (or approved
mirrors), and `cdn.functions.azure.com` for Functions bundles. Runtime data,
authoring client traffic, private SCM, and telemetry use private endpoints.
No public storage bootstrap is required.

### Module provenance and static validation

The wrapper uses pinned AVM modules for VNet, NAT/public egress IP, NSG, VM/NIC,
UAMIs, DNS/links, AMPLS, action group and monitoring private endpoint.
Module telemetry is disabled. New versions are not silently selected at deploy.
Resource schemas and AVM candidates were looked up before implementation.
The schema tool reported unsupported latest schemas for private DNS/AMPLS/action
group; published AVM contracts and Bicep compilation were used instead.
Direct resource-group declarations are retained for compile-time cross-RG scopes.
Existing runtime/storage/DI/private-endpoint/RBAC modules remain surgical local
modules rather than being replaced with behaviorally different AVM defaults.
Authoring storage is intentionally a separate small local module so its network
exception cannot bleed into production storage. Managed RunCommand has no AVM
module and uses its documented 2024-11-01 ARM schema with protected parameters.

Implementation verification:

- `Deploy-PrivateTest.ps1 -Mode Build`: both original and standalone Bicep
  entrypoints, plus example parameters, compiled successfully.
- PowerShell AST parsing: all scripts passed; unapproved Provision, directory
  Create, Bootstrap and original Infrastructure modes each stopped at their
  explicit approval guard.
- Existing Git Bash `-n runner-bootstrap.sh` and Python AST parsing with the
  Python 3.11 grammar passed. Constructed offline checks confirmed public DNS
  rejection, wrong-observer rejection, and admission cleanup after a simulated
  live-test failure. These checks do not invoke Azure.
- Source archive inspection found no PEM, venv/dependencies, local settings,
  generated corpus or evidence entries.
- Read-only regional image lookup confirmed Debian 12 Gen2 is Linux/x64/V2 and
  has no Marketplace plan. Subscription preflight is **blocked**, not passed:
  regional Web usages omitted EP1, requiring fresh attributable EP1 quota
  evidence. No Dedicated-plan quota is substituted.

These are syntax/contract checks, not live proof. Checkov was not present and
was not installed. No cloud
provisioning, directory writes, ARM what-if, model training, private networking
or end-to-end deployment success is claimed by these artifacts.

## Verification status and deployment boundary

The isolated synthetic reference was deployed, resolved 11 private endpoints,
indexed 13 Functions, invoked promoted Document Intelligence models, and completed
Event Grid/Durable/HL7 tests. This is not evidence for customer clinical accuracy,
load capacity, Power Platform connectivity or production release approval.

The architecture uses the Durable Functions **Azure Storage provider**, not Durable Task Scheduler. An Event Grid system topic filters `BlobCreated` events to the private data account's `ready` container and invokes the `ready_manifest` Function resource directly. That Function validates the event and starts Durable orchestration. Storage Queue is retained only as a recovery outbox. The API uses anonymous Functions HTTP bindings behind mandatory EasyAuth, never anonymous public access; no function keys are distributed.

The data Storage account keeps `publicNetworkAccess=Disabled` and
`defaultAction=Deny`, but enables the `AzureServices` network ACL bypass required
for Storage to configure and publish Event Grid notifications. Shared-key access,
public Blob access, and SAS-based application access remain disabled.

`main.bicep` preserves the existing-network entrypoint. The new `private-test.bicep` subscription wrapper creates its own private test network, DNS, runner and authoring environment. Bicep creates Azure RBAC assignments, not Entra directory roles. The explicitly gated `Initialize-TestIdentity.ps1` prepares isolated test registrations/application-role assignments only after directory/deployment approval. Neither template configures Power Platform connectivity or disaster recovery. There is no public-access bootstrap window.

### Approved model-authoring boundary

The user separately approved a **governed model-authoring environment** with documented
trusted-service access to its training storage, followed by model promotion into the
private runtime Document Intelligence resource. That authoring environment, its storage
permissions/network exceptions are included only in `private-test.bicep`, in a separate resource group. They are not added to `main.bicep` or its production storage accounts. Training and promotion remain explicit, billable operations after approval.

Do not apply authoring trusted-service exceptions to these runtime storage accounts.
Runtime Document Intelligence and storage public network access remain disabled, and
runtime storage trusted-service bypass remains `None`. The runtime managed identity
receives **no training-storage access**. Inference sends document **bytes** to the
runtime's private Document Intelligence endpoint rather than passing a training-blob
URL or SAS. Model promotion must be demonstrated using the separately approved
authoring/promotion identities and private runtime management/data paths before
enabling admission; it is not performed by this template.

## Resources and private endpoints

`infra\main.bicep` deploys to an **existing dedicated resource group** in global Azure. Sovereign-cloud identity and DNS names are not covered by this package.

| Resource | Configuration |
|---|---|
| Function App / plan | Python 3.11, Functions v4, Linux Elastic Premium EP1 default; EP2/EP3 configurable. One minimum instance, five maximum by default. HTTPS; TLS 1.2 minimum for site/SCM; FTP, basic SCM publishing, remote debugging and public ingress disabled. |
| Business storage | StorageV2, Standard_ZRS default (LRS configurable), shared-key and public access disabled, infrastructure encryption enabled. Containers: `incoming`, `ready`, `working`, `extracted`, `routed`, `human-review`, `jobs`. Queues: `fax-jobs`, `fax-jobs-poison`, `webjobs-blobtrigger-poison` (binding may create internal queues). |
| Host storage | Separate StorageV2 account; private blob, queue, table, file endpoints. Host and Durable use MI. `function-content` SMB share is explicitly created through ARM for the Premium platform content mount. |
| Document Intelligence | `FormRecognizer` S0 resource, unique custom subdomain, local/key auth disabled, public ingress disabled, `account` private endpoint. Models must be created separately on this resource. |
| Monitoring | Workspace-based Application Insights and Log Analytics; public ingestion and public queries disabled, local authentication disabled; both linked to a required existing AMPLS. |
| Alerts | Always-created HTTP 5xx and aggregate queue backlog metric alerts, configurable thresholds, required existing action group. |

There are **eight new private endpoints**: four host-storage services, two business-storage services, Document Intelligence, and Functions `sites`. The Functions DNS zone group also provisions the required SCM record. AMPLS private endpoints and their DNS are existing prerequisites, not among these eight.

Names use a deterministic `uniqueString(resourceGroup().id, namePrefix)` suffix:

- `func-<prefix>-<suffix>` / `plan-func-<prefix>-<suffix>`
- `stfaxhost<suffix>` / `stfaxdata<suffix>`
- `di-<prefix>-<suffix>`
- `appi-<prefix>-<suffix>` / `log-<prefix>-<suffix>`

Resource availability, policy, quotas, unique-name collisions, subnet capacity and zone redundancy must be checked by the authorized deployment team. No availability claim follows from compilation. The Premium plan is not zone-redundant simply because storage uses ZRS; availability-zone and region recovery requirements need a separate tested design.

## Existing network inputs: mandatory acceptance gates

1. Supply full IDs for `integrationSubnetId` and `privateEndpointSubnetId`. Both must be reachable in the approved topology; keep them distinct. The integration subnet must be delegated to **Microsoft.Web/serverFarms**, in the same region as the app, and have adequate free addresses for scaling and platform upgrades. A dedicated `/26` or larger integration subnet is recommended; validate actual plan/IP requirements. The endpoint subnet must allow private endpoint creation and the eight endpoints.
2. Supply existing, correctly linked private DNS zone IDs:

   | Parameter key | Private DNS zone |
   |---|---|
   | `blob` | `privatelink.blob.core.windows.net` |
   | `queue` | `privatelink.queue.core.windows.net` |
   | `table` | `privatelink.table.core.windows.net` |
   | `file` | `privatelink.file.core.windows.net` |
   | `sites` | `privatelink.azurewebsites.net` |
   | `cognitiveServices` | `privatelink.cognitiveservices.azure.com` |

3. Preserve the ordinary service hostnames in application URLs. Private DNS redirects them to endpoint IPs; do not use `privatelink` hostnames as application URLs. Validate resolution from **all three** contexts: the Function worker, deployment runner, and existing Power Platform private connectivity path.
4. Existing DNS forwarders/Private Resolver, zone links, UDRs, NSGs and firewalls must permit HTTPS 443 to storage, Document Intelligence, Function API and private SCM; the Function integration subnet must also permit **TCP 445 to the host storage file endpoint**. Custom DNS must resolve all host services, not just the blob endpoint. Private endpoint traffic may bypass ordinary App Service access restrictions; EasyAuth remains mandatory.
5. `vnetRouteAllEnabled=true` and `vnetContentShareEnabled=true` route application and Azure Files configuration traffic through the VNet. This does **not** create an outbound firewall. The existing network must provide controlled egress for Entra authentication/metadata, platform dependencies, certificate validation, and the Functions extension-bundle feed (`cdn.functions.azure.com`) when the bundle is not cached. A zero-internet-egress installation requires an independently validated extension-bundle supply strategy. Azure control planes and Entra endpoints are not claimed to be private data-plane endpoints.
6. Confirm that the existing Power Platform connector/environment can reach the API over the approved private route, preserves bearer tokens, and supplies the expected Entra audience/client/roles. A public HTTP connector without that connectivity will not work. Do not bypass this by making the API public.
7. The private runner must resolve both `<app>.azurewebsites.net` and `<app>.scm.azurewebsites.net` to private addresses, reach SCM on 443, and have controlled Entra/ARM egress. `Deploy.ps1 -Mode Code` rejects non-RFC1918 SCM resolution and never enables public or basic-auth publishing.

## Entra configuration and runtime authorization

Before deployment, the identity administrator must:

- Create a **single-tenant API registration**; provide `apiClientId`. `tenantId` defaults to the deployment subscription tenant. If intentionally overridden, all caller registrations and issuer checks must agree.
- Set `api.requestedAccessTokenVersion` to **2**, expose `api://<apiClientId>`, and define enabled `Fax.Submit` and `Fax.Review` application roles with `allowedMemberTypes: ["Application"]`. The isolated test script assigns uploader and reviewer roles to **different UAMIs**. A reviewer token must pass both the allowlist and runtime review-role check. Do not assign `Fax.Admin` to test callers.
- Assign these roles to the approved caller **enterprise application service principals**, grant administrator consent, and require assignment on the API enterprise app. The infrastructure deployer does not need Graph write access.
- Supply `allowedClientApplicationIds` using the caller's **application/client IDs** (`azp`/`appid`), not object IDs. EasyAuth checks this nonempty list.
- Optionally supply `allowedPrincipalObjectIds` using **service-principal or user object IDs** (`oid`); when present, both the client list **and** object list must match. The object list is limited to 13 GUIDs to remain within EasyAuth's documented 500-character limit.
- Request API access tokens for `api://<apiClientId>/.default`. No API client secret is needed to validate inbound bearer tokens; this package does not implement an interactive EasyAuth login flow or token store.

EasyAuth v2 requires authentication everywhere, returns 401 rather than redirecting, validates the tenant-specific `https://login.microsoftonline.com/<tenantId>/v2.0` issuer, and accepts only the configured API client-ID / Application ID URI audiences. The runtime is stricter: `ENTRA_API_AUDIENCE` is set to the API client GUID, the exact `aud` expected in v2 access tokens; tokens with a different audience representation are rejected by the runtime even if the platform audience list accepts them. EasyAuth's caller allowlist is **not an application-role check**. The Python API still checks the trusted injected principal's `auth_typ=aad`, tenant, principal object ID, issuer, audience and role, including mapped claim names. App Service supplies the read-only `WEBSITE_AUTH_ENABLED` environment value from platform authentication; do not spoof or override it. Runtime principal headers are trustworthy only behind this enforced boundary; never expose a development host that accepts spoofed headers.

## Exact application-setting contract

| Setting | Deployed value / purpose |
|---|---|
| `FUNCTIONS_WORKER_RUNTIME` | `python` |
| `FUNCTIONS_EXTENSION_VERSION` | `~4` |
| `DATA_STORAGE_ACCOUNT_URL` | Business account blob endpoint, `https://<account>.blob.core.windows.net/` |
| `DATA_QUEUE_URL` | Business account queue endpoint, `https://<account>.queue.core.windows.net/` |
| `DOCUMENT_INTELLIGENCE_ENDPOINT` | Custom-subdomain endpoint from the deployed resource |
| `AzureWebJobsStorage__blobServiceUri` | Private-resolved **host account** blob endpoint |
| `AzureWebJobsStorage__queueServiceUri` | Private-resolved **host account** queue endpoint |
| `AzureWebJobsStorage__tableServiceUri` | Private-resolved **host account** table endpoint |
| `AzureWebJobsStorage__credential` | `managedidentity`; no client ID means the system-assigned identity |
| `FaxQueueConnection__queueServiceUri` | Business queue endpoint used by `fax-jobs` trigger |
| `FaxQueueConnection__credential` | `managedidentity` |
| `FaxBlobConnection__blobServiceUri` / `FaxBlobConnection__queueServiceUri` | Legacy-compatible business binding endpoints retained for recovery functions |
| `FaxBlobConnection__credential` | `managedidentity`, dedicated runtime UAMI |
| `PROCESSING_ENABLED` | `false` initially and after scripted code deployment |
| `MAX_UPLOAD_BYTES` / `MAX_PAGES` | `20971520` / `100` |
| `ENTRA_TENANT_ID` / `ENTRA_API_AUDIENCE` | Tenant and exact expected audience (API registration client GUID for v2 tokens) |
| `WEBSITE_AUTH_AAD_ALLOWED_TENANTS` | The configured tenant |
| `WEBSITE_AUTH_AAD_REQUIRE_CLIENT_SERVICE_PRINCIPAL` | `true`, requiring `oid` (does not replace role checks) |
| `WEBSITE_CONTENTSHARE` | `function-content` |
| `WEBSITE_SKIP_CONTENTSHARE_VALIDATION` | `1`; bypass control-plane validation of the already ARM-created private share, not runtime mounting or network security |
| `WEBSITE_CONTENTAZUREFILECONNECTIONSTRING` | Sensitive platform content connection; see exception below |
| `WEBSITE_RUN_FROM_PACKAGE` | `1`; prebuilt ZIP mounted from the content share |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` / `ENABLE_ORYX_BUILD` | `false` / `false`; dependency restore happens on the builder |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Resource-generated telemetry endpoint string |
| `APPLICATIONINSIGHTS_AUTHENTICATION_STRING` | `Authorization=AAD` |

The runtime reads `pipeline/config.json` beside its Python module; this path is not an
environment-variable override. Caller application allowlisting is enforced by EasyAuth
`defaultAuthorizationPolicy.allowedApplications`, populated from the Bicep
`allowedClientApplicationIds` parameter—not by an unused runtime setting.

Do **not** add a literal `AzureWebJobsStorage`, `FaxQueueConnection` or `FaxBlobConnection` connection string: an exact setting overrides the identity-based prefix. The Durable extension must use this host connection and a compatible extension bundle (Durable MI support requires extension 2.7+; queue/blob identity requires Storage extension 5+). Do not set global `AZURE_CLIENT_SECRET`, `AZURE_CLIENT_ID` or other developer credential settings on the app. Runtime SDK clients use the system-assigned managed identity.

## Exact RBAC matrix

These assignments are created for the Function App's system-assigned principal. Stable role IDs are used to avoid localization ambiguities.

| Scope | Role and ID | Why |
|---|---|---|
| **Host storage account** | Storage Blob Data Owner — `b7e6dc6d-f1e8-4753-8033-0f276bb0955b` | Functions host locks/secrets and host-required storage; also covers Durable's blob contributor needs. This is not Azure subscription Owner. |
| **Host storage account** | Storage Queue Data Contributor — `974c5e8b-45b9-4653-ba55-5f855dd0fb88` | Durable task/control queues, created dynamically. |
| **Host storage account** | Storage Table Data Contributor — `0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3` | Durable history/instances and host diagnostics tables. |
| **Each of the six business containers**, individually | Storage Blob Data Contributor — `ba92f5b4-2d11-453d-a403-e96b0029c9fe` | Read/write job documents, conditional updates and leases; no account-wide business blob grant. |
| **Business queue service** | Storage Queue Data Contributor — `974c5e8b-45b9-4653-ba55-5f855dd0fb88` | Recovery outbox and poison queue only; grant only this queue service, not resource group Contributor. |
| **Business `ready` container** | Storage Blob Data Contributor — `ba92f5b4-2d11-453d-a403-e96b0029c9fe` | Read immutable Event Grid-selected ready manifests. |
| **Document Intelligence account** | Cognitive Services User — `a97b65f3-24c7-4388-baec-2e87135dc908` | Required by tested Document Intelligence data-plane authorization behavior; scoped only to the runtime DI account. |
| **Application Insights component** | Monitoring Metrics Publisher — `3913510d-42f4-4e42-8a64-420c390055eb` | Entra-authenticated Functions host telemetry ingestion. |
| **Azure Files content share** | **No runtime SMB RBAC role assignment** | The App Service Premium content mount uses the host account key; SMB share RBAC does not convert it into an MI mount. See below. |

Event Grid eliminates the polling Blob binding and its host management-role requirement. The runtime receives no resource-group Contributor, User Access Administrator, training-storage access, or broad subscription permissions.

### Document Intelligence runtime role

The runtime uses built-in **Cognitive Services User**, scoped only to the runtime
Document Intelligence account. Live testing showed that the narrower six-DataAction
custom role received HTTP 401 despite tokens having the correct tenant, audience,
and managed-identity object ID. The broader built-in role is therefore an explicit,
tested compatibility choice. It permits more data-plane operations than inference
alone; compensate with a dedicated runtime account, managed identity, model allowlist,
private endpoint, and deny runtime access to authoring storage.

| DataAction suffix after `Microsoft.CognitiveServices/accounts/FormRecognizer/` | Current backend REST operation |
|---|---|
| `documentmodels:analyze/action` | `POST /documentintelligence/documentModels/{id}:analyze` |
| `documentclassifiers:analyze/action` | `POST /documentintelligence/documentClassifiers/{id}:analyze` |
| `documentmodels/analyzeresults/read` | `GET /documentintelligence/documentModels/{id}/analyzeResults/{resultId}` |
| `documentclassifiers/analyzeresults/read` | `GET /documentintelligence/documentClassifiers/{id}/analyzeResults/{resultId}` |
| `documentmodels/read` | `GET /documentintelligence/documentModels/{id}` for schema preflight; provider also permits model listing |
| `documentclassifiers/read` | `GET /documentintelligence/documentClassifiers/{id}` for classifier preflight; provider also permits classifier listing |

Runtime requests use backend API version `2024-11-30`; classifier inference adds
`split=perPage`. The built-in role applies to the dedicated DI account, not
individual model IDs, so enforce the configured model allowlist in code and use
separate accounts for unrelated workloads. Its canonical definition ID is exposed
as `documentIntelligenceRuntimeRoleDefinitionId`.

Azure RBAC is additive. Verify that the identity has no inherited or resource-group
level Cognitive Services grants. The failed custom role may remain as an unassigned
definition after an incremental deployment; it grants nothing without an assignment
and can be removed separately by an authorized role administrator.

### Azure Files key exception — explicit, not “fully secretless”

Azure Functions Elastic Premium's platform Azure Files content mount does **not** support managed identity. The separate host storage account therefore permits shared-key authentication; business storage and Document Intelligence do not.

The template evaluates `hostStorage.listKeys().keys[0].value` **inside ARM** directly into `WEBSITE_CONTENTAZUREFILECONNECTIONSTRING`, an App Service app setting encrypted at rest. No actual key appears in source, parameter files, outputs, or script console output. This key is nevertheless visible to sufficiently privileged control-plane users and to application code through its environment. It is not a security boundary isolating the key from the worker. Limiting the key to a separate host account reduces business-storage exposure, but the host account itself contains sensitive Durable state.

`WEBSITE_SKIP_CONTENTSHARE_VALIDATION=1` prevents the platform's app-setting validation
from blocking creation when it cannot call the private storage endpoint from its
validation context. The **host storage module creates the share through ARM first**,
and the Function module depends on that completed deployment. This setting neither
creates the share nor grants access nor disables the storage firewall. A bad key,
incorrect share, broken private DNS or blocked TCP 445 still prevents runtime startup.
The live acceptance gate must verify a real private Azure Files mount and scale-out;
successful ARM deployment is not proof of either. No public bootstrap is used.

Consequences:

- The deploying principal needs host-account `Microsoft.Storage/storageAccounts/listKeys/action` in addition to resource writes. The runtime MI does not need that management action.
- Do not print app settings, request bodies, deployment debug traces, publishing profiles or HTTP authorization headers. Use approved log redaction and restrict deployment-history/config access.
- Rotate the host key through an approved runbook: move the content mount to the alternate valid key using a secure setting update, restart and verify mounting/scaling, rotate the previous key, then update the source/rotation approach consistently. Blindly regenerating the active key can stop the application. Redeploying this template rewrites the setting to `keys[0]`.
- If organizational policy requires Key Vault, replace the setting with a private-vault reference and a reviewed secret-provision/rotation path; add vault private networking, system-identity secret-read access, precreated content share and the documented content-share validation considerations. That alternate path is **not implemented** here. Do not pretend an SMB role assignment is a substitute.
- If policy prohibits **any** account keys, this approved Premium/content-share design is a blocker. Reassess hosting/deployment with the architecture owner rather than silently disabling shared-key access and breaking the content mount.

## Deployment principal and operator permissions

The ARM deployment identity needs scoped resource writes for Web, Storage, Cognitive Services, Insights, OperationalInsights and private endpoints, plus deployment operations. It also needs **`Microsoft.Authorization/roleDefinitions/write`** for the workload-RG-assignable inference role and `Microsoft.Authorization/roleAssignments/write` for the listed assignments. A practical initial setup is Contributor on the **new workload resource group**, plus Role Based Access Control Administrator (or User Access Administrator with appropriate conditions) at that scope. Verify that any delegated role-management conditions permit both the custom definition and its assignment. Contributor alone cannot create these roles or grant them.

On existing shared infrastructure it additionally needs:

- Integration subnet read and `Microsoft.Network/virtualNetworks/subnets/join/action`.
- Private-endpoint subnet read/join, plus permitted private endpoint connection approval on target resources.
- Private DNS zone read and the zone-group association permission (`Microsoft.Network/privateDnsZones/join/action`); an approved DNS-scoped custom role or Private DNS Zone Contributor as required by organizational deployment policy.
- Rights to create the two AMPLS `scopedResources` children and a nested deployment in the AMPLS resource group. The template uses the subscription/resource group parsed from `existingAmplsResourceId`; approve cross-subscription permissions in advance.
- Read/use access to the existing action group for alert association.

Pre-register resource providers through the platform team. No automatic role elevation or provider registration is performed.

A separate code-deployment identity needs site configuration update and SCM publish operations (`Microsoft.Web/sites/publish/action`) on the **Function App**, normally Website Contributor at that scope. It uses an Entra ARM token to private SCM, not an API `Fax.Submit` role or a publishing password. Give support operators Log Analytics Reader/Monitoring Reader only where needed, plus a private query path. No review-release API is implemented: reviewers must follow the separately governed operator workflow in the operations documentation, with specifically approved access rather than storage keys or broad portal grants.

## Monitoring is private only when the existing AMPLS network works

`existingAmplsResourceId` is mandatory. Its owner must preconfigure an approved private endpoint, private-only ingestion/query access modes, and DNS, including the required `privatelink.monitor.azure.com`, `privatelink.oms.opinsights.azure.com`, `privatelink.ods.opinsights.azure.com`, `privatelink.agentsvc.azure-automation.net`, and applicable storage private DNS entries. Share/link zones according to the existing central design; creating an unrelated competing AMPLS/zone can break other workloads.

The template links both new monitoring resources but does not change the shared scope, endpoint, access modes or DNS. App and workspace public ingestion/query remain disabled even if AMPLS connectivity is broken: **telemetry fails closed**, not back to public ingestion. Verify actual telemetry before go-live.

The required action group must already route to a staffed on-call destination and be tested. Supplied alerts:

- HTTP `Http5xx >= 5` in five minutes by default, evaluated each minute.
- Queue service `QueueMessageCount >= 100` over an hour by default, evaluated every five minutes. This capacity metric covers **all queues in the business account**, updates on its service cadence, and is not a per-queue latency/poison alert.

These do **not** guarantee detection of every failed orchestration, poison message, stuck review, Document Intelligence throttling, no-data telemetry outage, daily-cap drop, or completely unreachable private API. Before release, integrate a private synthetic/health probe, poison queue age/count checks, job-state SLA/reconciliation monitoring and a missing-telemetry heartbeat into the organization's existing monitoring. Alert thresholds and action group are configurable, but those external monitors are explicit outstanding gates—not silently skipped resources.

The default workspace daily cap is 5 GB and retention 30 days. A cap can interrupt telemetry; monitor cap utilization and tune to volume. Never include fax pages, extracted personal/health data, auth headers, tokens or document bodies in telemetry. Validate the app's logging/redaction behavior with synthetic data. Storage soft-delete/versioning is not a retention policy: business blobs have seven-day soft delete and versioning enabled; define and approve lifecycle/version cleanup, legal holds, backup/restore and Durable history purge schedules before production. No automatic business-data deletion is assumed by this template.

## Commands and staged release

Run from the package root in PowerShell 7.2+. The following are examples for an **authorized operator**, not actions performed during package generation.

### 1. Local build only — no Azure login required

```powershell
.\scripts\Deploy.ps1 -Mode Build
# Equivalent compiler checks:
az bicep build --file .\infra\main.bicep --outfile .\artifacts\main.json
az bicep build-params --file .\infra\main.example.bicepparam --outfile .\artifacts\main.parameters.json
```

The compiler is already present in the authoring environment (Bicep 0.43.8); it was not reinstalled or upgraded. If another builder has no compiler, install the approved Bicep version there. Build mode does not prove that a subscription will accept the resources.

### 2. Supply reviewed environment parameters

Copy `infra\main.example.bicepparam` to an environment-specific file. Replace all example GUIDs/resource IDs. Keep secrets out of it. Keep `processingEnabled=false`. Make maximum instance count at least minimum count. Confirm region, storage redundancy and all shared-resource ownership with the platform team.

```powershell
.\scripts\Deploy.ps1 -Mode Build -ParameterFile .\infra\main.prod.bicepparam
# Online read/validation actions, only after authorization:
.\scripts\Deploy.ps1 -Mode Validate -ParameterFile .\infra\main.prod.bicepparam -SubscriptionId '<subscription-guid>' -ResourceGroup '<workload-rg>'
.\scripts\Deploy.ps1 -Mode WhatIf -ParameterFile .\infra\main.prod.bicepparam -SubscriptionId '<subscription-guid>' -ResourceGroup '<workload-rg>'
# Online creation, after reviewing the change and obtaining approval:
.\scripts\Deploy.ps1 -Mode Infrastructure -DeploymentApproved -ParameterFile .\infra\main.prod.bicepparam -SubscriptionId '<subscription-guid>' -ResourceGroup '<workload-rg>'
```

What-if intentionally prints **resource IDs and change types only**, not sensitive app-setting values. Review the source and deployment through approved secure tooling for property-level approval. Infrastructure mode runs ARM validation then incremental deployment; it does not create the resource group, perform automatic destructive cleanup, or assume previous what-if is approval.

The Function's system identity is created before role assignments can be made. Its first host start may fail while assignments propagate; allow up to ten minutes or the platform's current propagation interval, then restart through the approved operator path and verify. Do not “fix” startup by adding connection strings or broad Owner permissions.

### 3. Build the application on Linux x64 / Python 3.11

The code source and requirements live in `app\`. `Package.ps1` requires Linux x64, Python 3.11 and PowerShell 7; it intentionally rejects a Windows binary-dependency build. It restores requirements into `.python_packages\lib\site-packages`, copies Python source/host metadata and the explicitly supplied config to `pipeline/config.json`, packages the **contents** rather than the `app` directory, and emits a SHA256 sidecar.

The custom packager uses an explicit source allowlist rather than interpreting
`.funcignore`. It excludes developer `.venv`/`venv`, existing `.python_packages`,
`__pycache__`, `tests`, `scripts`, `.pytest_cache`, `.git`, and `local.settings*`.
Only fresh Linux dependencies enter the generated `.python_packages` directory.

```powershell
# On the approved Linux builder, with package root as the working directory:
.\scripts\Package.ps1 -ConfigPath .\app\pipeline\config.json -PythonExecutable python3.11
```

The builder needs an approved package source or internal wheel mirror (`PIP_INDEX_URL`/`PIP_NO_INDEX`/`PIP_FIND_LINKS` as appropriate, supplied securely). No remote Oryx build or public PyPI access from the production Function is required. Build intermediates remain under `artifacts` and are cleaned after packaging. Add the archive and its digest to the approved immutable, access-controlled release store. SHA256 is an integrity check, not a signature or supply-chain attestation; use your normal signed-release process.

This script checks **JSON syntax only**, not semantic model/schema correctness or model quality. Replace any placeholder config, run the application's config validation and automated tests, and evaluate representative nonproduction faxes against the actual private Document Intelligence resource before enabling submissions.

### 4. Deploy from the private runner

```powershell
.\scripts\Deploy.ps1 -Mode Code -DeploymentApproved -SubscriptionId '<subscription-guid>' -ResourceGroup '<workload-rg>' -FunctionAppName '<functionAppName-output>' -PackagePath .\artifacts\fax-function.zip
```

Code mode checks the digest/package layout and private SCM resolution, sets `PROCESSING_ENABLED=false`, uploads with an in-memory Entra ARM bearer token to private SCM, polls the returned same-host deployment URL, and reports success only on SCM's successful terminal status. It does not print request headers, storage keys, response bodies or tokens; do not run it inside a credential-revealing debug/transcript wrapper. Azure CLI debug logging must remain disabled.

Success means **package deployment**, not application acceptance. If the process times out, inspect the private deployment state before retrying. This is a single-app deployment, not blue/green or an atomic Durable migration: drain/coordinate in-flight work and use backward-compatible orchestration changes. Disabling admission does not automatically cancel running jobs. Reapplying infrastructure may restart the app and resets admission according to the reviewed parameters.

### 5. Live acceptance and controlled admission

Before enabling processing, record:

1. Private DNS/HTTPS from Power Platform and runner; nonprivate ingress/SCM/data access denied.
2. Missing/expired/wrong-tenant/wrong-audience tokens rejected; wrong caller and missing role rejected; correct app roles accepted; principal-header spoofing rejected by EasyAuth boundary.
3. Host healthy on MI for blobs/queues/tables, Azure Files mounted privately on 445, worker restart and scale-out successful.
4. Runtime can access exactly the intended containers/queues and Document Intelligence; unrelated business storage denied.
5. Actual deployed model IDs, document schemas, page limits and routing validated; boundary and negative cases tested; retry, duplicate delivery, recovery and review workflows demonstrated.
6. End-to-end synthetic job tracked through queue/Durable/storage; retention and cleanup approved; existing jobs drained or compatible with new code.
7. Private telemetry ingested and queried; action group delivery and operational monitors verified; cap/no-data and poison/review monitoring assigned to an owner.
8. Approved release identity sets `PROCESSING_ENABLED=true` through a suppressed-output secure app-setting update. Do not change EasyAuth, private ingress, shared-key policies or caller allowlists to bypass validation.

Keep deployment logs, model/config hashes, acceptance evidence and rollback instructions in the authorized release record. The template's default remains safe/disabled.

## Deployment outputs

Top-level outputs are only nonsecret identifiers and endpoint URLs:

`functionAppName`, `functionAppResourceId`, `runtimePrincipalId`, `apiBaseUrl`, `scmHostName`, `dataStorageAccountName`, `hostStorageAccountName`, `dataStorageAccountUrl`, `dataQueueUrl`, `documentIntelligenceEndpoint`, `documentIntelligenceResourceId`, `documentIntelligenceRuntimeRoleDefinitionId`, `applicationInsightsResourceId`, `logAnalyticsWorkspaceResourceId`, `jobQueueName`, `poisonQueueName`.

No host key, SAS, access token, client secret, content connection string or App Insights connection string is a top-level deployment output. The internal monitoring module passes its endpoint connection string to the Function configuration; this is telemetry routing information, not a credential, and local ingestion authentication is disabled.

## Official references used

- [Functions secured storage and content share routing](https://learn.microsoft.com/azure/azure-functions/configure-networking-how-to)
- [Functions identity connections and role requirements](https://learn.microsoft.com/azure/azure-functions/manage-connections)
- [Durable Functions managed identity, Azure Storage provider](https://learn.microsoft.com/azure/azure-functions/durable/durable-functions-configure-managed-identity)
- [Functions app settings, including the Azure Files limitation](https://learn.microsoft.com/azure/azure-functions/functions-app-settings)
- [Functions infrastructure-as-code secured deployment](https://learn.microsoft.com/azure/azure-functions/functions-infrastructure-as-code)
- [EasyAuth Entra validation and allowlists](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad)
- [EasyAuth Bicep schema](https://learn.microsoft.com/azure/templates/microsoft.web/sites/config-authsettingsv2)
- [Azure Monitor Private Link configuration](https://learn.microsoft.com/azure/azure-monitor/fundamentals/private-link-configure)
- [Entra-authenticated Kudu package deployment](https://learn.microsoft.com/azure/app-service/deploy-zip)
- [Cognitive Services granular provider DataActions](https://learn.microsoft.com/azure/role-based-access-control/permissions/ai-machine-learning#microsoftcognitiveservices)
- [Custom role definition schema](https://learn.microsoft.com/azure/templates/microsoft.authorization/roledefinitions)

Azure Bicep schema tools were also consulted for Web sites/config, storage accounts, private endpoints and Cognitive Services accounts before generation.
