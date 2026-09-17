# Synthetic Document Intelligence model authoring

**TEST ONLY. Cloud writes have not been authorized or performed by these tools.**
The artifacts contain no real people or actionable prescriptions. Zero dose,
imaginary medication, `SIMULATION ONLY` route and `NEVER ADMINISTER` frequency
are deliberate. These synthetic documents measure engineering behavior, not
clinical accuracy, medical safety, or sufficient clinical sample size.

## Corpus and model separation

`e2e/fixtures.py generate --root e2e/generated/synthetic-v1` makes raster PDFs and
multi-frame TIFFs, a ground-truth manifest, normalized label polygons and field
schemas. Use the same dependency versions for reproducibility. A corpus directory
with an existing manifest is never overwritten. `self-check` verifies hashes,
page counts, label coverage/bounds and train/held-out separation.

Default training counts: six documents per extraction type, six examples of
cover and eighteen examples of body. Three body-type classes each have six
documents. Documents are copied into purpose-specific prefixes:

| Prefix | Model / class |
|---|---|
| `train/page/cover/`, `train/page/body/` | page classifier: cover/body |
| `train/type/exam/`, `train/type/order/`, `train/type/rx/` | whole-body classifier |
| `train/extract/{type}/` | one document-specific template/neural extractor |
| `development/` | six development documents, never uploaded for training |
| `heldout/` | 22 independent cases, never uploaded for training |

Shared values: PatientId, PatientName, DateOfBirth, EncounterId, DocumentDate,
ProviderId. Exam adds ExamName/ResultText; order adds OrderCode/OrderDescription;
rx adds MedicationCode/MedicationName/Dose/Route/Frequency. Date strings are ISO
8601. All field schemas intentionally use string/not-specified. Runtime performs
canonical validation and HL7 typed mapping.

Do not tune against held-out results and then continue calling that corpus
held-out. Make a new independently versioned holdout after development tuning.
The default covers only one synthetic form family with small position variation;
neural build mode does not turn it into a representative clinical dataset.

## Private authoring and promotion

Run on the private Azure-side managed-identity runner, **not** a workstation.
Assign its user-assigned MI and put its real client ID in the configuration.
The CLI uses `ManagedIdentityCredential(client_id=...)`, never keys, SAS or
developer-credential fallback. It does not deploy resources or change firewalls.

Required infrastructure:

* Separate authoring DI and private runtime DI, both accessible to the runner
  through private endpoints/DNS.
* Separate training Storage with selected networks and trusted Azure services
  bypass; enable the **authoring DI system-assigned identity**, granting it
  Storage Blob Data Reader on the training container. Trusted-services access is
  not the same thing as the runner's private endpoint access.
* Runner MI: Cognitive Services User (or verified custom equivalent permitting
  build/analyze/copy) on both DI resources; Storage Blob Data Contributor on the
  training container. Scope permissions narrowly and verify API-specific RBAC.
* Runner subnet/DNS can resolve both DI and training Blob service names privately.
  Outbound Entra/managed-identity dependencies must remain available.
* Persist the `--state` path on durable private runner storage between invocations.
  Only one process may use a journal/prefix at a time. Snapshot evidence before
  rebuilding. No journal may enter a public artifact feed.

Copy `e2e/training.example.json` to a restricted runner configuration and fill
**real** ARM IDs, endpoints, model IDs, container URL and unique prefix. Empty
templates deliberately fail preflight; no fictional deployed IDs are supplied.
The authoring/runtime model IDs should be different, immutable versioned IDs.

```powershell
python e2e\train.py preflight --config training.json --root e2e\generated\synthetic-v1 --state e2e\state\training.json --dry-run
# Only after explicit authorization, on the Azure private runner:
python e2e\train.py prepare --config training.json --root e2e\generated\synthetic-v1 --state e2e\state\training.json --execute
python e2e\train.py build --config training.json --root e2e\generated\synthetic-v1 --state e2e\state\training.json --execute
python e2e\train.py promote --config training.json --root e2e\generated\synthetic-v1 --state e2e\state\training.json --model-output artifacts\promoted-models.json --execute
python e2e\evaluate_development.py --config training.json --promoted-models artifacts\promoted-models.json --root e2e\generated\synthetic-v1 --state e2e\state\development.json --output artifacts\development-evidence.json --execute
```

`prepare` actually calls `prebuilt-layout:analyze` using **2024-11-30**, polls
Operation-Location, retains the complete `.ocr.json` result, and replaces
generated geometric labels with actual OCR words/polygons normalized by page
width/height. A word-region mismatch fails preparation; it never manufactures OCR.
The operator must inspect those mismatches and rendered label positions before
training. The label format is `document`, `labels[{label,key,value[
{page,text,boundingBoxes}]}]`; fields.json is `fields[{fieldKey,fieldType,
fieldFormat}]`. No legacy `:train` call or fabricated OCR response is used.
Each training document has its correctly named sidecars (`file.pdf.ocr.json`,
`file.pdf.labels.json`). Classifier prefixes require documents and OCR, not
extraction labels. Development/held-out files are excluded by an explicit
manifest split check, not a filename wildcard over the entire corpus.

`build` submits two `documentClassifiers:build` calls with `docTypes` mapped to
`azureBlobSource` prefixes, then three `documentModels:build` calls. Both
template/neural are supported; default template, maximum .5 training hours per
extractor. It GET-verifies extraction field schemas and saves returned `docTypes`.
Use returned docType names for runtime `resultDocType`, not guessed model IDs.
The five-document minimum is an API prerequisite, **not** an accuracy guarantee.

Pass optional `--model-output <path>` to `promote --execute` to export verified
model settings for deployment integration. The output's `modelSettings` holds
classifier IDs and extraction `resultDocType` names selected from actual
GET-verified schemas. It deliberately has `enabled:false` and blank
`calibrationId`; it is a merge fragment, not an activatable pipeline config.
Corpus (`--root`), operation journal (`--state`), and model output locations
are independently configurable. Keep journal/output outside a replaceable source
checkout on the private runner.

### Generate a complete runtime and runner configuration

`e2e/configure_runtime.py` is the local-only merge tool. It imports the actual
runtime `validate_config`/v2 contract and HL7 profile selector rather than
duplicating their acceptance rules. Supply real IDs from deployment outputs,
verified promotion output and the genuine development evidence produced by
`evaluate_development.py`. The merge tool verifies evidence status, content
digest, actual model IDs and corpus manifest hash; a placeholder reference
alone cannot create an enabled configuration.

```powershell
python e2e\configure_runtime.py --promoted-models artifacts\promoted-models.json `
  --root e2e\generated\synthetic-v1 `
  --runtime-output artifacts\runtime-config.json --runner-output artifacts\runner-config.json `
  --tenant-id $TenantId --uploader-client-id $UploaderClientId `
  --uploader-principal-id $UploaderPrincipalId --observer-client-id $AuthorClientId `
  --reviewer-client-id $ReviewerClientId --storage-url $StorageUrl `
  --api-url $ApiUrl --api-audience $ApiAudience `
  --development-evidence artifacts\development-evidence.json `
  --deployment-version $DeployedBuildVersion --variant happy --enable-config
```

Without `--enable-config`, the output file stays disabled. With it, only the local
configuration becomes enabled; **the tool never changes the app's
PROCESSING_ENABLED setting or performs deployment/cloud writes**. Deployment
must keep that setting false until the separately authorized live window and
disable it in a `finally`/cleanup path afterward.

### Bounded real-model development qualification

`evaluate_development.py` uses the six **development** documents only, never
training or held-out test documents. With `--execute` it calls the real promoted
runtime page classifier (`perPage`), type classifier (`auto`) and corresponding
extractor, then checks actual class/whole-body coverage, required-field scalar
types, normalized values and **fixed confidence >=0.8**. Extraction is evaluated
against its labeled document type independently; a wrong type-classifier result
still fails the overall check. It does not use fixture IDs inside runtime.

The command has a separate persistent **18-POST hard cap**: 12 classifier
page-passes and six custom-extraction pages for the six one-page development
documents. Failed/ambiguous POSTs are not blindly resubmitted; operations and raw
results persist in its journal. All six cases must pass. Per-case outcomes,
confidence values, expected/actual fields, document/result hashes, actual model
IDs, elapsed times and a content-addressed `synthetic-dev-sha256-...` evidence ID
are recorded. Failed or partial evaluations receive **no activation evidence
ID**; configuration generation refuses them and processing remains disabled.
The tool never changes app settings, retrains, adjusts thresholds, or inspects
held-out results for tuning. `--dry-run` performs no network calls.

This is genuine **small-sample synthetic development evidence**, not statistical
confidence calibration, population qualification, clinical acceptance or proof
that unknown/mixed documents will always be rejected. These development samples
have body pages; cover performance is independently exercised by held-out cases.
The runtime's `calibrationId` points to this explicitly scoped artifact rather
than a made-up operator label. Accepted evidence can be reused for separately
pinned route/HL7 configuration variants for the same models and corpus.

Variants are `happy`, `no-route`, `multi-route`, `hl7-not-applicable`, `hl7-error`.
Every source retains explicit mappings for all three types. The error variant
uses a policy-admitted 64-character sending-application namespace that the real
strict HL7 parser rejects; it does not omit a mapping or inject a mock error.
All use only trusted integration sources `clinic-a`/`clinic-b`. The matching
runner output pins config SHA256, actual model versions and `selectedCases`.
`run.py` honors selectedCases by default; explicit --case can narrow further.
Generate/deploy separate variants with fresh evidence directories to exercise
conflicting source/type behavior honestly. The HL7-error variant deliberately
omits the clinic-b exam mapping, and verifies the runtime's missing-mapping
policy rejects it when publishing. This is configuration testing, not a
production fault-injection switch.

`promote` calls target `:authorizeCopy` and source `/{id}:copyTo` for both
classifiers and extractors, then GET-verifies each target. Copy access tokens
exist only in memory and never enter the journal. Operation locations, terminal
results, model versions and submission counters persist. GET retries are bounded;
POSTs are not blindly retried. A crash while submitting requires operator
reconciliation to avoid duplicate billed builds. Resume pending operations using
the same config/corpus/state. The persistent default budget is 50 POST attempts,
with a hard 100-POST maximum (including OCR and authorization). There is no
automatic retraining, quota escalation or budget reset.
The default six-example corpus needs approximately 24 unique OCR submissions,
five builds and ten copy/authorization submissions; larger corpora can hit the
cap intentionally.

### Initial campaign usage plan

The runner may opt into `--train-count 5`: this creates 50 training references
but only 20 unique Layout documents/pages, requiring 35 planned authoring/copy
POSTs and 18 separate development POSTs (53 total). Development remains six
documents; held-out cases remain 22. Layout page inference is approximately
$0.20 and development inference $0.216 at the cited rates. The existing exported
corpus uses six examples; generate a new corpus/state for the five-example plan
rather than reusing its hashes or evidence. Only the three extractor builds
expose the configured `maxTrainingHours` limit; a five-build hours allowance is
a conservative budget estimate, not an enforced classifier training-time cap.

The default corpus has **60 training document references but only 24 unique
document hashes/pages**. Hash-cached OCR makes 24 layout submissions. Initial
authoring performs two classifier builds and three extractor builds; promotion
performs five copy authorizations and five copy operations: **39 planned POSTs
total**. There are no automatic POST retries. Three extractor
`maxTrainingHours=.5` settings total 1.5 V100-equivalent training hours; do not
interpret that as a guaranteed wall-clock/billing cap for classifier training.
No automatic retrain loop exists. Preflight prints these counts.

The 20 valid held-out faxes contain 27 input pages and 20 potential body pages:
at most 47 classifier page-passes and 20 custom-extraction pages if all proceed.
Two invalid cases should not reach inference; retries still need monitoring.
The first smoke submission should be only `exam-pdf-body`; expand to twelve
happy paths and then independently pinned variant subsets after checking costs.

At the verified rates recorded in [test-budget.md](test-budget.md), 24 layout
pages cost approximately $0.24, 47 classifier page-passes $0.141, and 20 custom
pages $0.60: approximately **$0.981 in planned page inference** before training,
compute, storage, networking and retries. Training allowance is not assumed.
Monitor actual training charges separately against the approved $100 total
budget and base infrastructure reservation. Budget approval is not deployment
authorization.
The additional required development qualification performs 18 inference POSTs
under its own cap (57 planned DI POSTs including authoring/promotion), costing
approximately $0.216 in page inference at the cited rates. Combined planned
authoring OCR, development checks and full held-out inference is approximately
$1.197 before training/infrastructure/retries. No free training allowance is
assumed.

## Official format/API references

Verified against Microsoft's published examples and API reference:

* [Custom build and labeling prerequisites](https://learn.microsoft.com/azure/ai-services/document-intelligence/how-to-guides/build-a-custom-model?view=doc-intel-4.0.0)
* [Classifier build 2024-11-30](https://learn.microsoft.com/rest/api/aiservices/document-classifiers/build-classifier?view=rest-aiservices-v4.0%20(2024-11-30))
* [Extraction build 2024-11-30](https://learn.microsoft.com/rest/api/aiservices/document-models/build-model?view=rest-aiservices-v4.0%20(2024-11-30))
* [Classifier copy authorization](https://learn.microsoft.com/rest/api/aiservices/document-classifiers/authorize-classifier-copy?view=rest-aiservices-v4.0%20(2024-11-30))
* [Model copy](https://learn.microsoft.com/rest/api/aiservices/document-models/copy-model-to?view=rest-aiservices-v4.0%20(2024-11-30))
* [Microsoft label asset example](https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/Data/invoice/Invoice_1.pdf.labels.json)
* [Microsoft field/label type definitions](https://github.com/microsoft/OCR-Form-Tools/blob/master/src/models/applicationState.ts)

The historical Microsoft asset examples establish label/fields shape; current
OCR is always obtained from the actual v4 layout service, not copied from old
`readResults` examples. Service-side acceptance, learned quality, regional
availability and private-network copy completion remain **live prerequisites**.
