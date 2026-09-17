# Azure Fax Production — private architecture

This is an **editable target architecture**, not evidence of deployment or measured accuracy.
The original `Azure-Fax-Architecture` drawing and this production drawing now
contain the same four pages. The historical Event Grid drawing is synchronized
to the subsequently approved **private Queue-based intake** rather than retaining
two conflicting delivery architectures.

**Design-only extensions:** dedicated fax-type classification, human-reviewed
release, business validation and selected-data HL7 transformation/validation.
These stages are **not implemented by the existing Python/Bicep package**.
The existing API/configuration documentation remains the authority for executable
behavior. See the [success criteria and proposed stage contracts](../docs/success-criteria.md).

## Open the diagrams

| File | Purpose |
| --- | --- |
| `Azure-Fax-Production.drawio` | Uncompressed, editable draw.io XML with four pages, native shapes, editable text, connected edges, and embedded official Azure SVG icons. |
| `topology.svg` | Self-contained overview of the private runtime and supporting services. |
| `workflow.svg` | Separate page/type classification, automatic/human approval, shared HL7 validation and manifest-last workflow. |
| `criteria.svg` | Proposed success criteria matrix; targets are not measured results. |
| `reporting.svg` | High-value reporting metrics with breakouts, inputs, cadence and actions; proposed, not live measurements. |
| `preview.html` | Offline, self-contained browser gallery containing all four SVG diagrams. |
| `ICON-SOURCES.md` | Official icon source, usage attribution, and preservation details. |
| `icons\manifest.json` | Per-icon archive entry and SHA-256; source archive SHA-256. |
| `generate-diagrams.cjs` | Dependency-free source for the four pages, gallery and original-drawing mirror when that folder exists. |
| `acquire-icons.ps1` | Reacquires the selected official icon bytes from the public Microsoft archive. |
| `validate-diagrams.ps1` | Local structural, reference, and embedded-icon validation. |

Open the `.drawio` file using **diagrams.net Desktop** or an approved local draw.io editor. The file is not a screenshot: service cards, icons, labels, decisions, and arrows can be selected and edited. The two Functions cards on the topology page represent **logical intake and worker roles**, not a requirement to create two Function Apps. All processing activities use the private runtime network path.

Each SVG and the HTML gallery can be viewed without reaching any remote service. The `.drawio` file likewise embeds its icons; the `icons` directory is required only to regenerate the files. No diagram content was sent to an online diagram-rendering service.

## Reading the design

### 1. Private intake and reliable job delivery

Power Apps invokes Power Automate. The existing, supported Power Platform private VNet connectivity must be bound to the correct environment/connector, resolve the Function's private hostname, and reach its private endpoint. A private endpoint alone does not make an arbitrary public Power Automate connector private.

Function App public network access is disabled. Entra EasyAuth validates caller authentication, and the API enforces the configured caller/application authorization. Intake and job-status requests use the same private, authenticated API under `/api/faxes`. Source authorization is bound to the caller's `tid:oid` identity mapping, not trusted directly from caller-supplied source metadata. Entra authentication is not replaced by network isolation.

The intake role persists the **original fax** and then an immutable ready manifest. Storage emits `Microsoft.Storage.BlobCreated`; an Event Grid system topic filters to `ready/*.json` and invokes the Function. Intake returns an asynchronous job identifier. A durable outbox in the `jobs` container, private **`fax-jobs` Azure Storage Queue**, and a **five-minute repair timer** provide recovery while preserving idempotency.

The Event Grid Function validates the exact storage host, event type, subject and Blob URL before reading the ready manifest, then directly starts/resumes the idempotent Durable Functions orchestration. The queue trigger is a recovery path only. Durable history contains only `faxId`, step, and state: raw AI responses, long-running-operation data, and document bytes reside in private `working` storage instead.

### 2. Classification, cover handling, and extraction

The runtime reads bytes from private Blob Storage and sends **document bytes** to Document Intelligence through its private endpoint, using REST API `2024-11-30` and `base64Source`. It does not give Document Intelligence a Blob URL and assume the service can traverse the customer's private endpoint.

The page classifier uses REST **`split=perPage`** (SDK concept: `splitMode`).
When a cover is identified, the worker physically removes the classified cover
pages and produces body-only PDF bytes. No cover means all body pages remain.
The **separate Classify type step** then identifies the logical fax as exam
record, order, Rx or another approved type, from body evidence rather than a
filename. Require a known type and calibrated type confidence; unknown,
low-confidence, mixed or conflicting results go to review.
This selects the versioned extractor, required-field schema and HL7 mapping
policy. The current code derives type from page labels; a dedicated type-stage
implementation and models still need to be added.

“Body-only” and “normalized body” describe page selection and format normalization only. **Malware scanning is not implemented or certified by this pipeline**; cover removal does not constitute antivirus inspection or content-safety certification.

The document-type-specific extraction model returns expected field values and per-field confidence. The versioned expected-field schema is the authority for field completeness and validation; whatever fields the model happened to return are not sufficient to define success.

### 3. Automatic fail-closed acceptance gate

For automatic approval, every expected field must:

1. Be present and nonempty where required.
2. Have a value valid under the document-type schema and business constraints.
3. Have a finite, valid confidence in the range 0–1.
4. Have confidence **greater than or equal to `0.80`**.

The predicate is an **AND across all expected fields**, including every required nested leaf and array item, not an average, maximum, majority vote, or document-level classifier confidence.

| Example | Gate result |
| --- | --- |
| Every expected value valid; lowest confidence `0.80` | Accept |
| Any confidence `0.7999` | Reject |
| Required field absent, null, or invalid | Reject |
| Field confidence missing or null | Reject |
| NaN, infinity, or out-of-range confidence | Reject |
| High classifier confidence but a field score missing | Reject |
| Unsupported type or unusable analysis document | No publication; reject or operational failure as defined by the runtime contract |

Missing confidence must never be silently substituted with a passing value. Failed processing is not a successful rejection or acceptance: keep operational failure states distinguishable from a completed human-review decision.

In the **proposed human path**, a named reviewer verifies/corrects the fax type,
body page plan and required values against the original. Independently validate
corrected values and record an audited approval without altering the original
machine confidence. Mark provenance as human-approved, not automatically accepted.
No unresolved review case enters the approved-data contract.

### 4. Strictly separated outputs

**Pending review:** retain the original and review evidence with scores, reasons
and page map in `human-review`. This is not successful completion and creates no
accepted release manifest. The drawing now explicitly proposes an audited review
and approval path; the existing application still has **no reviewed-release API**.

**Both approval paths:** merge into a canonical approved-data contract with
automatic/human provenance. Evaluate all source/content/type routing rules and
freeze the output plan. If selected outputs require HL7, transform the selected
fields with a versioned receiver-specific mapping and validate every generated
message for profile conformance and semantic/source fidelity. Human approval
does not skip this stage. Failed transformations or validation hold the job;
corrections must repeat approval and transformation. Only an explicit versioned
NotApplicable decision can skip HL7; a missing required mapping cannot.

Persist approved primary JSON and all planned JSON/HL7 outputs only after these
checks. Commit the release manifest **last**, with all artifact hashes,
approval/mapping/model versions and HL7 validation evidence or explicit N/A.
Only then set Completed. Intermediate files are not consumable without that
manifest. Use immutable revision-scoped identities for human corrections and
retry safety. Blob publication is not a receiver ACK or proof of clinical delivery.
The existing code publishes JSON only; this paragraph describes the target.

The infrastructure contract provides six business containers: **`incoming`, `working`, `extracted`, `routed`, `human-review`, and `jobs`**. `working` holds intermediate/normalized body artifacts and sensitive AI/LRO state; `extracted` holds primary accepted JSON and the accepted release manifest; `jobs` holds job status and the durable intake outbox. Queue delivery uses **`fax-jobs`** with **`fax-jobs-poison`** for poison-message handling. The diagrams use “primary” and “status” as logical roles, not extra container names.

## Private platform dependencies

- **VNet:** reference existing resource IDs. Function integration and private endpoints use distinct, appropriate subnets; private endpoints are the entry points to PaaS services, not a claim that PaaS workers run inside the customer subnet.
- **Storage:** separate business data from the Function/Durable **host storage account**. Host dependencies require private **blob, queue, table, and file** endpoints and DNS. Business Blob and Queue access is also private. The host Premium Files content-share key requirement is an explicit hosting exception; application data access still uses managed identity.
- **Identity:** use the Function App's system-assigned managed identity and least-privilege RBAC scoped to the needed storage resources and runtime inference resource. Document Intelligence uses the custom **`Fax Document Intelligence Inference <RG-hash>`** role, not the broader built-in Cognitive Services User role. Its six explicit DataActions cover classify, analyze, operation polling, and model/classifier metadata read/list; control-plane Actions are empty. The definition is resource-group-assignable, but the runtime assignment is scoped only to the Document Intelligence account. Model-authoring/promotion permissions belong to a separately governed release identity. Platform-required content-share authentication is a distinct hosting detail and must not be confused with the application's MI-based data access.
- **Private DNS:** existing private DNS zone IDs are parameters. Resolve the normal service hostnames through the approved private DNS chain. Required families include `privatelink.azurewebsites.net` (both app and SCM records), `privatelink.blob.core.windows.net`, `privatelink.queue.core.windows.net`, `privatelink.table.core.windows.net`, `privatelink.file.core.windows.net`, and `privatelink.cognitiveservices.azure.com`.
- **Monitoring:** workspace-based Application Insights and Log Analytics attach to a **required existing Azure Monitor Private Link Scope (AMPLS)** with an existing reachable private endpoint and private DNS. Public ingestion/query are disabled. The relevant existing Monitor private DNS zones include `privatelink.monitor.azure.com`, `privatelink.oms.opinsights.azure.com`, `privatelink.ods.opinsights.azure.com`, `privatelink.agentsvc.azure-automation.net`, and applicable Blob private DNS. A **required existing action group** handles operational alerts. Validate DNS effects before attaching to shared zones.
- **Build/deploy:** a private runner must resolve and reach the Function SCM private hostname. Build Python dependencies into the deployment package using the target-compatible environment. Do not rely on public SCM deployment or public runtime package downloads.

The runtime infrastructure creates **eight new private endpoints**: four for host storage (blob/queue/table/file), two for business storage (blob/queue), one for Document Intelligence (`account`), and one for the Function App (`sites`, including SCM private DNS resolution). The existing AMPLS endpoint is a prerequisite, not a ninth new endpoint. Document Intelligence uses S0 with a custom subdomain, imported models, disabled local authentication, and disabled public network access. Processing remains disabled until validated model configuration is supplied. EasyAuth is single-tenant with a caller allowlist and fixed application roles.

“Fully private” here means the **workload data plane and operational data paths** described above have no public endpoint fallback. Power Apps/Automate SaaS UI, Entra token issuance, supported platform control planes, and release governance have separate service prerequisites. Do not promise that those global Microsoft service planes have been relocated into the customer's VNet. Validate allowed platform dependencies, DNS, and routes under the organization's network policy.

## Governed authoring is separate from inference

Train and evaluate the classifier and document-type extraction models in a **separate governed authoring environment**. The user-approved authoring design uses **documented trusted-service access to training storage**, with the authoring identity, storage permissions, network exception, and dataset governance scoped explicitly to that environment. This is an approved authoring-only access path, not customer-private-endpoint traversal by Document Intelligence.

Promote approved, versioned models into the private runtime Document Intelligence resource using the supported model copy/import capability for the selected API and model type. **Runtime public network access remains disabled throughout**. The runtime has **no training-storage access** and performs byte-based inference through its private endpoint. This architecture package documents the authoring boundary; the runtime infrastructure does not have to deploy the separate authoring environment. Detailed setup, trusted-service configuration, and model release procedures belong to the main package's training/model documentation.

Do **not** represent a customer's private Blob endpoint as a mechanism by which the Document Intelligence service can fetch private training bytes. A Document Intelligence private endpoint controls client access **to** the service; it does not inject the service into the customer's VNet for outbound training-data retrieval.

Release validation must establish that the chosen model types support the selected
copy/import operation, destination authorization, region/API compatibility and
private-runtime configuration. Page/type classifiers and extraction models need
separate evaluation; model promotion is not an in-runtime training workflow.

## Success criteria matrix

Page 3 includes proposed quality, clinical-error, fail-closed, HL7, publication,
latency, review and automation-yield targets. The [detailed matrix](../docs/success-criteria.md)
defines denominators, sample sizes, statistical bounds, strata and sign-off.
All mandatory criteria must pass; unmeasured or under-sampled results are not
qualified. Do not equate an 80% model confidence score with 80% measured accuracy.

## Reporting metrics

Page 4 breaks out 12 reporting groups: demand, outcome funnel, audited quality,
type/cover accuracy, field rejection drivers, human review, HL7, publication
reliability, latency, controls, cost and reporting trust/drift. Each card identifies
high-value KPIs, breakdowns, source facts, cadence and operational actions.
The [metric dictionary](../docs/reporting-metrics.md) defines denominators,
cohorts, unfinished-job handling, ownership and privacy controls. Reporting
remains a proposed design; no dashboard, telemetry pipeline or alerts were deployed.

## Regeneration and validation

From the repository root:

```powershell
node '.\Azure-Fax-Production\architecture\generate-diagrams.cjs'
& '.\Azure-Fax-Production\architecture\validate-diagrams.ps1'
```

Node.js and built-in PowerShell/.NET are sufficient; no packages are installed. Regeneration is deterministic from the preserved icon bytes and layout source. Changes made in the draw.io editor are not reverse-synchronized into the generator.

To intentionally reacquire the public icons:

```powershell
& '.\Azure-Fax-Production\architecture\acquire-icons.ps1'
```

The download is staged only inside this architecture directory, selected SVG entries are extracted without modification, and the archive is deleted after successful extraction. Reacquisition can change hashes if Microsoft updates the contents at the same URL; review the manifest change before regenerating.
