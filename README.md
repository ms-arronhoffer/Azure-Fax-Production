# Private Azure fax processing

A deployment package for Power Apps / Power Automate intake, Python Azure
Functions processing, Azure AI Document Intelligence and private Blob Storage.

The Python application implements separate page and fax-type classification,
human-reviewed release, and pre-completion HL7 transformation for both approval
paths. The four-page drawing and [success criteria](docs/success-criteria.md)
describe the implemented workflow and the population-level release gates.

## Business behavior

1. Upload a PDF or multipage TIFF through the private, Entra-protected API.
2. Preserve the original and commit an immutable `ready` manifest.
3. Use Event Grid to validate admission and start Durable Functions directly;
   retain Storage Queue as the recovery outbox.
4. Classify pages, remove confidently identified covers, classify the fax type,
   and select the document-specific extractor.
5. Require **every expected field** to be present, valid and **>= 80% confidence**.
   Missing fields or scores reject the auto-read; exactly 80% passes.
6. On rejection, store the original and draft result/reasons in a separate
   `human-review` container. Do not publish accepted outputs.
7. On acceptance or approved review, build and validate the configured HL7 output
   (or enforce an explicit `NotApplicable` policy).
8. Write primary JSON/HL7 and zero or more complete routed copies selected by
   **all** matching source/content/document-type rules.
9. Record completion only after every required output has been persisted.

The overall field confidence is the minimum expected-field confidence, not an
average. A high classification score cannot override a low field score.
Stored results include the minimum score and field-specific rejection reasons.
Consumers must wait for a completed manifest and verify its artifact hashes;
individual blobs are not a transaction boundary.

## Approved architecture decisions

- **Fully private runtime:** existing private connectivity from Power Platform
  to the Function API; private endpoints for runtime services.
- **Event Grid primary admission:** `BlobCreated` events are filtered to immutable
  `ready/*.json` manifests and invoke the Function, which starts Durable directly.
  Storage Queue remains a private recovery outbox, not the primary delivery path.
  The Function public ingress is default-deny with only the `AzureEventGrid`
  service tag allowed; application and SCM access remain private. Failed Event
  Grid deliveries are retained in `event-grid-deadletter`.
- **Durable Functions:** asynchronous processing and persisted job progression.
- **Managed identity and scoped RBAC:** separate business and host storage;
  explicit Entra business API roles.
- **Separate model-authoring environment:** approved trusted-service training
  storage access; models promoted into the private runtime.
- **Versioned configuration:** actual model IDs, expected-field schemas,
  authorized sources and routing predicates must be supplied before processing.

## Contents

| Location | Description |
|---|---|
| [architecture/](architecture/) | New editable Azure-icon architecture and SVG preview |
| [infra/](infra/) | Bicep template, modules and example deployment parameters |
| [app/](app/) | Commented Python Functions application, configuration and tests |
| [scripts/](scripts/) | Infrastructure/code packaging and deployment helpers |
| [power-platform/](power-platform/) | Importable upload/status custom-connector definition; replace tenant/host values |
| [Deployment guide](docs/deployment.md) | Ordered deployment, private runner and release steps |
| [How to use](docs/how-to-use.md) | Submit, monitor, review and consume a fax; add schemas and retrain models |
| [Infrastructure details](docs/infrastructure.md) | Concrete parameters, resources, role assignments and networking |
| [API/configuration contract](docs/api.md) | Actual implemented routes, payloads, configuration and validation |
| [Power Platform setup](docs/power-platform.md) | Canvas app, custom connector, flows and solution promotion |
| [Identity and RBAC](docs/rbac.md) | Runtime/deployer/reviewer/authoring permissions and Entra setup |
| [Custom models](docs/models.md) | Training, labeling, evaluation, copy/promotion and lifecycle |
| [Operations](docs/operations.md) | Acceptance matrix, failure handling, retention and readiness gates |
| [Validation record](docs/validation.md) | Checks actually performed and remaining cloud verification |
| [Success criteria matrix](docs/success-criteria.md) | Proposed accuracy/reliability targets, sample sizes and shared HL7 release requirements |
| [Reporting metrics](docs/reporting-metrics.md) | Page 4 KPI breakouts, formulas, reporting cadence, ownership and privacy requirements |
| [Azure Workbooks](infra/workbooks/) | Deployable operations and quality/release dashboard definitions |
| [Formal cost estimate](docs/Azure-Fax-Processing-Cost-Estimate.docx) | Presentation-ready monthly base and per-10,000-page planning estimate |
| [Cost estimate methodology](docs/cost-estimate.md) | Reproducible assumptions, formulas, sensitivity and pricing sources |

## Start here

Open the [Azure-icon diagram](architecture/Azure-Fax-Production.drawio) in
draw.io, or view the [offline preview](architecture/preview.html).

For day-to-day fax submission and review, start with the
[How to use guide](docs/how-to-use.md).

1. Read the [deployment guide](docs/deployment.md) and confirm the network,
   identity, licensing, quota and trained-model prerequisites.
2. Set up [API application roles](docs/rbac.md) and approved source identities.
3. Train/evaluate/promote the [models](docs/models.md).
4. Prepare environment-specific Bicep parameters and runtime configuration.
5. Validate, deploy infrastructure, package/deploy code from a private runner,
   and execute the [acceptance matrix](docs/operations.md).
6. Configure the [Power Platform solution](docs/power-platform.md) and enable
   admission only after release approval.

## What this package does not claim

The isolated synthetic environment described in [validation](docs/validation.md)
was deployed and exercised. The reusable package does not create a Power Platform
solution or supply customer model IDs/labeled documents. Tenant-specific Entra,
network/DNS and Power Platform integration still require environment-specific work.

This is a production-oriented baseline, **not a clinically qualified production
release**. The live synthetic infrastructure and workflow pass, but the current
small held-out exact-field result is below the mandatory release threshold.
Retrain and independently qualify customer models before enabling production
admission. See [validation](docs/validation.md) for the exact evidence.

To export the source, diagrams and guides, run `scripts\Export-Source.ps1`.
That source archive is **not** the runnable Linux Functions ZIP; build the
deployment artifact separately with `scripts\Package.ps1` after supplying the
approved runtime model configuration.

## Sensitive data

Never commit actual faxes, OCR results, access tokens, connection secrets or
reviewer corrections. Keep local settings and environment-specific sensitive
artifacts outside source control. Architecture icons are used only to describe
the Microsoft services; see the architecture attribution file for their terms.
