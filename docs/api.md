# Private synthetic fax runtime/API contract (version 2)

Python 3.11, Azure Functions v4/Python v2, Durable Functions, Document Intelligence
REST `2024-11-30`. This implementation is for a **private synthetic demonstration**.
Its HL7 is not certified for a clinical receiver. No cloud provisioning or live
qualification is implied by passing local tests.

## Authentication and authorization

All HTTP endpoints MUST be behind private App Service EasyAuth, with public access
disabled and unauthenticated requests rejected. Runtime `ANONYMOUS` Functions auth
means **no Functions keys**; it does not mean unauthenticated application access.
Code checks `WEBSITE_AUTH_ENABLED=true`, EasyAuth `auth_typ=aad`, exact configured
tenant/v2 issuer/audience, `tid`, `oid`, and roles. Both mapped and short claim names
are supported. Only EasyAuth may strip and regenerate `X-MS-CLIENT-PRINCIPAL`.
Never forward caller-supplied principal headers or enable a local auth bypass.
Unit tests mock the boundary in memory only; live tests must use real tokens.

| Role | Permission |
|---|---|
| `Fax.Submit` | Binary HTTP submission; exact authenticated `tid:oid` source allow-list |
| `Fax.Admin` | Read any safe status/facts; not submit or release |
| `Fax.Review` | Read restricted review evidence and confirm/correct/release; read status/facts |

An owner can read its status/facts. Others get 404 rather than disclosure.
A shared integration's token identifies that integration, **not the end-user or
fax sender**. A delegated Power Automate connection can identify the connection
user rather than the app user. Source labels are authorized routing labels, never
proof of the sender's identity.

## Configuration and environment

`app/pipeline/config.json` is the disabled version-2 template. New admissions require
`schemaVersion: 2`, `enabled: true`, `example: false`, valid deployed models, and live
classifier/model-schema preflight. Jobs snapshot this configuration. Model IDs and
the extractor's actual `resultDocType` must be discovered from trained model metadata,
not guessed. Older configurations cannot admit new work. Older job records/history
can still be read/replayed, but missing canonical/profile requirements fail closed
to review instead of emitting legacy unchecked output.
`validate_config` retains legacy parsing for existing snapshot diagnostics, but
the actual `admission_config` boundary additionally calls `validate_v2` **before
any service preflight or job creation**. It rejects v1 with
`configuration_upgrade_required` (HTTP admission returns configuration-unavailable
503). Migration requires new trained v2 models/config and reingestion under a new
fax identity; never mutate historical snapshots or overwrite prior v1 artifacts.

Required nonsecret settings:

```text
DATA_STORAGE_ACCOUNT_URL=https://<data-account>.blob.core.windows.net
DATA_QUEUE_URL=https://<data-account>.queue.core.windows.net
DOCUMENT_INTELLIGENCE_ENDPOINT=https://<custom-subdomain>.cognitiveservices.azure.com
AzureWebJobsStorage__blobServiceUri=https://<host-account>.blob.core.windows.net
AzureWebJobsStorage__queueServiceUri=https://<host-account>.queue.core.windows.net
AzureWebJobsStorage__tableServiceUri=https://<host-account>.table.core.windows.net
AzureWebJobsStorage__credential=managedidentity
FaxQueueConnection__queueServiceUri=https://<data-account>.queue.core.windows.net
FaxQueueConnection__credential=managedidentity
FaxBlobConnection__blobServiceUri=https://<data-account>.blob.core.windows.net
FaxBlobConnection__queueServiceUri=https://<data-account>.queue.core.windows.net
FaxBlobConnection__credential=managedidentity
ENTRA_TENANT_ID=<tenant-GUID>
ENTRA_API_AUDIENCE=<exact-token-audience>
PROCESSING_ENABLED=false
MAX_UPLOAD_BYTES=20971520
MAX_PAGES=100
```

Normal Azure service origins resolve through private DNS; arbitrary hosts, URL
paths, query strings, SAS, and credentials in endpoints are rejected. The deployed
runtime uses managed identity, not developer credential fallback. The isolated
data account contains `incoming`, `ready`, `working`, `jobs`, `extracted`, `routed`,
and `human-review`. Durable storage and Event Grid delivery also require their normal
host-storage identity permissions. Runtime authoring/model-training permissions
are not required.

## Direct upload: original first, commit manifest last

The Python `ready_manifest` function is an **Event Grid trigger** with a Durable
client binding:

```python
@app.event_grid_trigger(arg_name="event")
@app.durable_client_input(client_name="client")
```

The system-topic subscription accepts only `Microsoft.Storage.BlobCreated` events
whose subject begins `/blobServices/default/containers/ready/blobs/`. The Function
then independently validates the exact subject, HTTPS account hostname, absence of
query/fragment, and `ready/<uuid>.json` path before reading the manifest. It starts
the idempotent Durable instance directly. The `fax-jobs` queue is retained only as
an outbox/recovery channel.

Only the configured trusted integration uploader may write `incoming` and `ready`
(plus the runtime's required access). This is a storage RBAC/private-network boundary:
the manifest does not authenticate an uploader or contain an end-user identity.
Do not give arbitrary end users Blob write access or treat blob metadata as
authentication. Separate uploader identity, no account keys/SAS/shared access,
and restrictive container-scoped permissions are deployment prerequisites.

1. Allocate a canonical lower-case UUID `faxId`. The integration owns this ID's
   idempotence globally; do not reuse an ID for another fax.
2. Upload the entire PDF/TIFF, using a create-only write, to the `incoming`
   container with exact blob name `<faxId>/original`. Complete the block list before
   proceeding. Compute SHA-256 over those exact original bytes.
3. Create (never overwrite) `ready/<faxId>.json` with this exact shape:

```json
{
  "schemaVersion": 1,
  "faxId": "33333333-3333-4333-8333-333333333333",
  "source": "clinic-a",
  "mime": "application/pdf",
  "original": {
    "container": "incoming",
    "name": "33333333-3333-4333-8333-333333333333/original"
  },
  "sha256": "<64 lower-case hexadecimal characters; replace with real digest>"
}
```

No extra keys, duplicate JSON keys, URLs, traversal, arbitrary containers/paths,
invalid UUIDs, or nonfinite numbers are allowed. The sample digest placeholder is
not a valid manifest. Limit 16 KiB for the ready JSON and configured maximum 20 MiB
for original bytes. MIME must be `application/pdf` or `image/tiff` and match the
signature. The trigger name, manifest ID, and original location must agree exactly.

The source must be in:

```json
{
  "directUpload": {
    "enabled": true,
    "trustedUploader": "<tenant-GUID>:<integration-service-principal-object-GUID>",
    "allowedSources": ["clinic-a", "clinic-b"]
  }
}
```

`trustedUploader` is the **configured integration boundary**, not evidence recovered
from the upload. Audit records label this distinction explicitly. When direct upload
is enabled its allowed sources should match the authorized integration lanes.

Admission reads and verifies the complete original before creating a job or queuing
work. An immutable private `working/<faxId>/admitted-original` snapshot protects
processing/review against subsequent integration-side replacement. SHA, source,
MIME, owner and normalized ready-manifest digest participate in conflict checks.
Identical retries converge on the existing job; a conflicting retry cannot change
the first admission. No partial/absent original can start an orchestration.

The leased job is the existing durable outbox: `originalReady` plus
`admissionCommitted` must be true before the queue starter accepts direct-upload
work. Queue messages contain only `{"faxId":"..."}`. Failed enqueue is repaired by
the existing timer or identical upload retry. A crash before admission commit is
repaired by retrying the ready trigger, never by assuming the mere existence of an
incoming blob means acceptance. Invalid manifests remain trigger failures; the
host retries and then preserves a Blob-trigger poison notification in
`webjobs-blobtrigger-poison`. Operators must inspect/replay privately after remediation.
Disabled processing throws rather than acknowledging/dropping ready work.

Policy/conflict rejections with a safe canonical trigger filename also persist
immutable attempt evidence at
`jobs/_admission-rejections/<faxId>/<SHA256-of-manifest-bytes>.json`. Query
`GET /api/faxes/{faxId}/admissions/{manifestSha256}` as the configured integration
with `Fax.Submit`, or with `Fax.Admin`/`Fax.Review`. It returns `status: RejectedAttempt`,
safe reason code, digest/byte scope and config digest, never uploaded values or an
asserted uploader identity. This is positive evidence of a failed attempt, **not**
an inference from a missing job and not a terminal claim about a later successful
retry. For oversized markers the digest covers the bounded first 16,385 bytes and
is explicitly labeled `bounded-trigger-read`. Invalid trigger names, unavailable
config/storage, and disabled processing may have no receipt: that is inconclusive,
not proof of rejection. A missing original is retried as a storage availability
failure and is not falsely labeled a verified policy rejection.

## HTTP submission (backward-compatible binary API)

`POST /api/faxes` requires `Fax.Submit`, the configured `sources[tid:oid]` allow-list,
and these headers:

```http
Content-Type: application/pdf
x-fax-source: clinic-a
idempotency-key: 57d1bd00-4501-41f9-b7d7-3fbd89fcdca8
```

The body is the complete binary PDF/TIFF, **not JSON or a URL**. IDs are derived
deterministically from authenticated actor plus UUID idempotency key. Successful
response (202) contains `faxId` and relative `statusUrl`; headers include `Location`,
`Retry-After: 10`, and `Cache-Control: no-store`. No Durable management keys or SAS
are exposed. The original and private processing snapshot are immutable.

| HTTP code | Meaning |
|---|---|
| 202 | Original persisted and queue handoff succeeded; poll |
| 400 | Invalid request/signature/UUID |
| 403 | Identity, role, or source not authorized |
| 409 | Same identity/key with different payload/source/MIME |
| 413 | Empty or oversized body |
| 503 | Disabled/unavailable; retry same key, not a fresh fax |

`GET /api/faxes/{faxId}` returns safe `faxId`, `status`, `createdAt`, `updatedAt`,
optional machine `reason`, `manifest`, `publication`, `completionMode`,
`reviewRevision`, and a provenance **channel/identity-meaning description**. No raw
field values, source content, reviewer reason, ownership ID, fingerprints, or model
results are returned. Statuses remain `Accepted`, `Processing`, `Publishing`,
`PendingHumanReview`, `Failed`, and `Completed`. `Failed` retains review evidence
where readable original bytes are available. A reviewed completion is `Completed`
with `completionMode: Reviewed`, not a fabricated machine-confidence pass.

## Two separate classifiers and required extraction schema

```json
{
  "classifier": {
    "id": "<trained-page-classifier>",
    "minimumConfidence": 0.8,
    "coverClasses": ["cover"],
    "bodyClasses": {"body": "body"}
  },
  "faxTypeClassifier": {
    "id": "<trained-fax-type-classifier>",
    "minimumConfidence": 0.8,
    "calibrationId": "<independent-calibration-artifact-reference>",
    "classes": {"exam": "exam", "order": "order", "rx": "rx"}
  }
}
```

1. Normalize PDF/TIFF. Encrypted/unreadable/excessive-page/decompression-bomb input
   is held. TIFF maximums are 25M pixels/page, 100M total; normalized PDF 50 MiB.
2. Page classifier uses `split=perPage`. Every original page must have exactly one
   cover/body assignment at/above its configured threshold (at least .8).
   Unknown, duplicated, missing, ambiguous, or all-cover pages go to review.
3. Persist original page mapping; physically remove covers into `body.pdf`.
4. A **different classifier** classifies that logical body using `split=auto`,
   allowing the service to return separate logical documents/types instead of
   forcing a single result.
   Exactly one exam/order/rx document, covering every body page, with calibrated
   threshold at least .8 is required. Unknown/multiple/missing/low-confidence
   results go to review. `unknown` is held out, not an accepted class.
5. Select that type's extractor; require exactly one returned document matching
   the configured, discovered `resultDocType`.

`calibrationId` is a required deployment evidence reference, not proof computed by
the runtime. The training/qualification process must choose the threshold using an
independent calibration set and evaluate unknown/mixed forms on held-out data.
A confidently incorrect model prediction cannot be discovered from its score alone.

`documentTypes` names and ordered `expectedFields` are shared in
`pipeline/contracts.py`:

| Type | Required fields (common first) |
|---|---|
| All | PatientId, PatientName, DateOfBirth, EncounterId, DocumentDate, ProviderId |
| exam | ExamName, ResultText |
| order | OrderCode, OrderDescription |
| rx | MedicationCode, MedicationName, Dose, Route, Frequency |

Each type also requires `modelId` and actual `resultDocType`. Every required field
must be present, nonblank/readable, and have numeric finite confidence **>= .80**;
no mean, rounding, truthiness test, or tolerance. `.7999` fails; exactly `.8` passes.
Missing confidence fails. Scores and raw fields are preserved unchanged.
The existing generic gate still supports nested expected-field arrays, but the
version-2 canonical schema is the fixed flat field list above.

The v2 canonical schema is typed: every field except Dose requires a nonblank
string; identifiers/names/result text do not accept booleans or numbers. Dates
additionally require exact ISO `YYYY-MM-DD`. Dose accepts a nonnegative plain
decimal JSON number or decimal string; `0`, `0.0`, `"0"` and `"0.0"` are preserved
without coercion. Boolean Dose, signed/scientific notation strings, appended units,
null/missing/blank/container/nonfinite values and unreadable sentinels fail.
The generic confidence gate still correctly distinguishes false/zero from absence:
a false or numeric text-field value is rejected later as a **type mismatch**, not
silently treated as missing. HL7 imposes further datatype/profile constraints.

## Canonical and demonstration HL7 completion

Both automatic and reviewed paths build `canonical.json` with `schemaVersion: 2`,
fax ID, revision (automatic 0, reviewed 1), source label, document type,
completion mode, full scalar `values`, original page mapping and provenance.
Both paths must pass the **same** canonical and source/type profile validation
before creating output artifacts.

Every source/type needs an explicit `hl7Profiles[source][documentType]` mapping:

```json
{
  "profile": "demo-oru-r01-v1",
  "sendingApplication": "FAXDEMO",
  "sendingFacility": "SYNTHETIC",
  "receivingApplication": "DEMO",
  "receivingFacility": "SYNTHETIC",
  "processingId": "T"
}
```

Use `demo-orm-o01-v1` for order or `demo-rde-o11-v1` for rx. **Rx additionally
requires `doseUnits`, e.g. `"TESTUNIT"`**, configured for that synthetic source lane;
the runtime will not infer units from absent schema fields. A source with variable
or unknown dose units must not use this profile. Dose must be a nonnegative plain
decimal value/string, not `"10 mg"` or a boolean; zero is preserved. Missing/wrong
profile, unsafe source configuration, invalid dose/date/HL7 datatype fail closed.
Only explicit `{"status":"NotApplicable","reason":"<nonempty policy reason>"}`
permits JSON-only completion. There is no default or missing-profile bypass.

| Canonical input | Demo mapping |
|---|---|
| PatientId, PatientName, DateOfBirth | PID.3, PID.5, PID.7 |
| EncounterId | PV1.19; PV1.2 is literal U (unknown class) |
| DocumentDate | MSH.7; OBR.7 or ORC.9 as applicable |
| ProviderId | OBR.16 or ORC.12 |
| exam ExamName, ResultText | OBR.4/OBX.3 text, OBX.5; ORU^R01^ORU_R01 |
| order OrderCode/OrderDescription | OBR.4; ORM^O01^ORM_O01 |
| rx MedicationCode/MedicationName, Dose | RXE.2, RXE.3; RDE^O11^RDE_O11 |
| rx Frequency, Route, configured doseUnits | RXE.1/TQ1.3, RXR.1, RXE.5 |

All messages are UTF-8, CR-segment-delimited HL7 2.5.1 with MSH.11=`T`, a stable
fax/revision control ID, and escaped separators/control characters. PatientName
is carried as one literal name component, not guessed into family/given parts.
Exam OBX.11 uses U, not a claim of clinically finalized results. Order/rx ORC.1
uses demo-profile literal NW; these messages are not authorization to place orders.

`hl7apy==1.3.5` parses messages and strictly validates each segment's standard
datatypes/required fields. Our local profile additionally validates exact segment
sequence, version, type, source headers and control identity. This is intentionally
**not a claim that every standard message-group or receiver rule is validated**:
the library's global ORM_O01 choice-group validator incorrectly demands mutually
exclusive RQD/OBR alternatives together. No clinical receiver is contacted.
Receiver-specific identifiers, terminology, units, workflow, acknowledgment,
transport and conformance qualification remain outside this demo.

## Authorized human review

`GET /api/faxes/{faxId}/review` requires `Fax.Review`. It returns restricted original
machine evidence (raw fields, original scores, page plan, classification results,
reason and original reference), job status/revision, and `ETag: "review-0"`.
This endpoint intentionally contains PHI-shaped synthetic values and must not feed
telemetry. `GET /api/faxes/{faxId}/review/original` requires the same `Fax.Review`
role and an existing review record, and downloads the immutable original as an
attachment with no-store/nosniff/sandbox headers. No public URL/SAS or broad reviewer
Blob Reader role is necessary. This preserves an actual human's ability to inspect
the source rather than approving OCR text alone.

`POST /api/faxes/{faxId}/review` requires the same role, enabled processing, and
`If-Match` exactly equal to the GET ETag:

```json
{
  "documentType": "exam",
  "bodyPages": [2],
  "values": {
    "PatientId": "SYN-001", "PatientName": "Synthetic Person",
    "DateOfBirth": "1970-01-01", "EncounterId": "SYN-E-001",
    "DocumentDate": "2026-09-15", "ProviderId": "SYN-P-001",
    "ExamName": "Synthetic exam", "ResultText": "Synthetic result"
  },
  "reason": "Independently inspected the synthetic original and corrected extraction"
}
```

The reviewer confirms/corrects **all values, type, and page plan**, not just a low
field. Body pages are unique increasing original 1-based page numbers, nonempty
and in bounds; their complement is cover pages. Request limit 512 KiB; reason
1–2000 nonblank characters. Extra fields, invalid plans, absent values, wrong
schema, missing mapping or invalid HL7 cannot release. Unreadable originals cannot
be released through a fabricated page plan. This endpoint cannot modify source,
config, scores, or raw machine evidence.

An exclusive job lease plus revision ETag enforces optimistic concurrency. Immutable
`human-review/<faxId>/revisions/000001/decision.json` reserves the revision and records
authenticated reviewer tenant/oid, server UTC time, reason, complete corrected values
and original machine evidence. A reserved revision can only be retried by the same
reviewer with the same semantic request. No confidence is increased or fabricated.
Successful completion consumes that revision; a stale second reviewer receives 409.
Retries after a partial output or manifest/status-write failure converge on the
same immutable revision. GET status can be used after an ambiguous HTTP response.

If a reserved but uncommitted review needs correction, an authorized reviewer may
call `POST /api/faxes/{faxId}/review/reservations/{revision}/abandon` with the current
`If-Match` and exact JSON `{"reason":"<nonblank correction explanation>"}` (16 KiB
request limit). The exclusive lease ensures no publisher is active. The endpoint
refuses to abandon any revision whose completion manifest exists, writes immutable
`abandoned.json` with reviewer oid/time/reason, and advances the ETag. For example,
abandoning reserved revision 1 changes `"review-0"` to `"review-1"`; the next review
POST uses the new ETag and publishes revision 2, never reusing revision 1 paths.
Old decisions and partial artifacts remain evidence and have no completion marker.
GET review exposes a pending reservation's decision, manifest-committed flag, and
abandonment-recorded flag for recovery. If abandonment evidence writes but status
update fails, retry abandonment; the recorded abandonment blocks resuming that
revision even before status recovery. A committed manifest must instead be
reconciled by retrying its original accepted review request; it cannot be abandoned.

| Review response | Meaning |
|---|---|
| 200 | Evidence fetched or reviewed completion committed |
| 403 | No authoritative EasyAuth `Fax.Review` identity |
| 404 | Fax/review not found |
| 409 | Stale ETag, nonpending job, or already reserved conflicting revision |
| 413 | Oversized request |
| 422 | Invalid plan/values/profile/JSON or unreadable original |
| 428 | Missing If-Match |
| 503 | Disabled/unavailable; retry same identity/body/ETag |

## Publication and immutable commit markers

Automatic artifacts retain the original JSON-route contract:
`extracted/<faxId>/content.json`, plus `canonical.json`, optional `content.hl7`,
and `manifest.json`. Matched routes receive JSON and optional HL7 under
`routed/<routeId>/<faxId>/`. Routing remains zero-or-more fan-out; all conditions
within a rule are AND. Field equality is type-exact, so false is not numeric zero.

Reviewed artifacts use a **different immutable identity**:
`extracted/<faxId>/revisions/000001/{canonical.json,body.pdf,content.json,content.hl7,manifest.json}`.
The HL7 file is omitted only for configured NotApplicable. Matched routes use
`routed/<routeId>/<faxId>/revisions/000001/`. Reviewed content/field routing is
computed from corrected canonical values, never unreliable original OCR text.

The completed manifest is always written **last** after all routed artifacts.
Mandatory `report.json` is also written first and included in the manifest's
hash-bound artifact set. Initial pending-review reports precede `review.json`,
which references their hashes. Report-write failure therefore leaves no new
commit marker or terminal status. Ambiguous report-write retries use an already
persisted post-payload preparation timestamp and identical bytes; status-write failure after a
manifest commit safely repairs status without recreating reports.
Consumers MUST read the job's committed manifest pointer and verify artifact hashes,
not watch individual file creation. No multi-blob transaction is claimed. A partial
fan-out has no completion marker and must not be consumed. Automatic and review
artifacts never overwrite each other. Review manifests carry `reviewAuthorized:true`,
original `gatePassed` (which may remain false), decision pointer, configuration hash,
profile disposition, routes, and provenance. `Completed` never means a model achieved
ground-truth accuracy.

## Reporting without PHI telemetry

`GET /api/faxes/{faxId}/facts` uses the same owner/Admin/Review authorization and
returns export-friendly operational facts: IDs, status/timestamps, completion mode,
revision, counts, confidence minimum/type score when available, safe reason code,
ingestion channel and readiness flags. It explicitly labels quality truth as
**not measured; requires independent labeled evaluation**. It returns no raw values,
content, reviewer reason, identity, raw sender metadata or extraction result.

Additional bounded reporting dimensions use the **same contract** in facts API and
manifest-bound export records:

| Field | Meaning |
|---|---|
| `sourceLabel` | Only the configured, allow-listed source label (maximum 64 safe characters); never a sender identity or raw uploaded metadata |
| `configSha256` | SHA-256 of the exact configuration snapshot, not the configuration contents |
| `configuredModelIds` | `pageClassifier`, `faxTypeClassifier`, and `extractor` for the currently known type when available |
| `modelIds` | Corresponding IDs of models whose analysis calls were actually accepted; omitted when not observed |
| `documentIntelligenceApiVersion` | Configured runtime REST contract `2024-11-30` |
| `calibrationId` | Bounded configured calibration reference; not a quality metric or verification claim |
| `configuredHl7Profile` | Known configured demo-profile ID for the source/type |
| `hl7Disposition` | Observed `Validated`, `NotApplicable`, or `Rejected`; omitted before evaluation |
| `hl7Profile`, `hl7Version`, `hl7MessageType` | Validated demo profile/version/message identity where known; no configured NotApplicable reason is exported |
| `matchedRouteCount` | Number of destinations selected by the applicable routing decision, including zero |
| `publishedRouteCount` | Only present after a completed manifest is ready; not inferred from partial blob creation |
| `phaseTimings` | Bounded `prepare`, `classify`, `removeCovers`, `type`, `extract`, `publish`, `review` maps with stored `startedAt`, `completedAt`, `decisionAt`, and/or `preparedAt` |
| `completionPreparedAt` | Persisted only after all required payload/routed writes, before report/manifest writes; **not actual completion or an SLA endpoint** |
| `completionPreparationRevision` | Revision associated with that preparation timestamp (automatic 0) |
| `completionClock` | Manifest container/name and metadata property `lastModified`, the authoritative commit clock |
| `completedAt`, `completedAtSource` | Actual manifest last-modified timestamp in terminal job facts, with source `manifestLastModified`; not present in the earlier immutable report snapshot |

Model IDs are deployment/version identifiers, not invented provider version
numbers. After human type correction the actually used extractor can differ from
the configured extractor for the corrected type; both dimensions retain that
distinction. Checkpoint timestamps are persisted server observations and reused
on retries, not claims of precise service-side execution duration. Publish/review
decision time is distinct from final storage-write time. Missing historical
measurements remain absent rather than becoming zero or fabricated timings.

Immutable `report.json` records are persisted beside automatic completion, original
review evidence, and reviewed revision output. They support status/count/duration,
review workload and automation-rate reporting. They are operational observations,
not quality truth. Immutable completion reports have `snapshotPhase: completion-prepared`,
omit `completedAt`, and point at manifest metadata via `completionClock`. Their
`updatedAt`/`completionPreparedAt` describe post-payload preparation, not actual completion.
After the manifest is written, terminal job facts read its storage `lastModified`
into `completedAt`, labeled `completedAtSource: manifestLastModified`. Status recovery
reads that same immutable manifest metadata; it does not replace commit time with
retry time or publication/reviewer start time. For end-to-end latency/SLA use that
actual commit clock, not preparation or decision timestamps. A report claiming
completion is consumable only through its completed manifest, never as a standalone
precommit blob. Legacy unqualified completion timestamps are omitted from facts.
Durable inputs/outputs
and application logs contain only safe
state/IDs/machine reason codes; SDK error text, original bytes, full LRO URLs and
extraction results stay in restricted storage. Fax IDs remain sensitive operational
correlation identifiers even without direct patient fields.

## Local verification and remaining live gates

Use the repository's existing Python 3.11 environment:

```powershell
Set-Location .\app
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -c "import function_app; print([f.get_function_name() for f in function_app.app.get_functions()])"
```

Tests cover immutable retry/conflict, missing-original barriers, unsafe manifests,
source enforcement, classifier/field boundaries, held-out type rejection, real
review code behind a mocked-only-in-tests EasyAuth boundary, concurrency, corrected
plans/types, preserved scores, partial publication recovery, HL7 and explicit
NotApplicable behavior. Binding indexing verifies the Event Grid and Durable client
bindings; it is not a live Functions host/identity/network test.

Before any live qualification: obtain explicit cloud-write authorization; provision
the isolated private synthetic environment; verify private DNS/routes/RBAC and
EasyAuth real tokens/roles; train/discover both classifiers and extractors; calibrate
the type threshold independently; test unknown/mixed forms; verify Event Grid and
poison/retry recovery; inspect immutable outputs with independently labeled expected
results. None of these quality/network/security live gates is replaced by unit tests.
