# Synthetic fax test evidence

**No cloud writes or live inference have been performed.** Local fixture checks
and contract tests are not deployment acceptance. All remote submissions and
training require explicit `--execute`. Default/`--dry-run` validates local inputs
only and does not authenticate or contact Azure.

## Local usage

Use existing Pillow/pypdf dependencies if present. If dependencies are missing,
prefer an isolated interpreter outside OneDrive; never copy certificates, `.pem`,
credentials or an environment into the source archive. `e2e/requirements.txt` is
independent of backend requirements. The private runner image should install it
when built. The examples below assume `python` is your explicitly selected
interpreter and the working directory is Azure-Fax-Production:

```powershell
python e2e\fixtures.py generate --root e2e\generated\synthetic-v1
python e2e\fixtures.py self-check --root e2e\generated\synthetic-v1
python -m unittest discover -s e2e -p test_local.py -v
```

Generated corpus/evidence/state paths are ignored by Git, deliberately retained
locally, and excluded from the source-only ZIP. The private runner should
generate its corpus from `e2e/fixtures.py` after bootstrap; a complete separately
packaged synthetic corpus may instead be transferred privately when identical
binary hashes across hosts are required. Do not transfer ground-truth manifests
without the exact corresponding PDF/TIFF files. The handoff archive
`e2e/generated/synthetic-faxes.zip` contains the complete generated corpus only,
not an interpreter, cache, credentials or certificates.
The generator refuses to overwrite an existing completed corpus.
See [synthetic-models.md](synthetic-models.md) for model authoring and promotion.

## Exact direct-upload boundary

Write container `incoming`, blob `{faxId}/original`, first, using a canonical
lowercase UUID. Upload it immutably with content type and `sha256`, `source`,
`mime`, `testonly` metadata. Then write container `ready`, blob `{faxId}.json`,
last, with this **exact** JSON key set:

```json
{
  "schemaVersion": 1,
  "faxId": "00000000-0000-0000-0000-000000000001",
  "source": "clinic-a",
  "mime": "application/pdf",
  "original": {"container": "incoming", "name": "00000000-0000-0000-0000-000000000001/original"},
  "sha256": "replace-with-real-64-character-lowercase-sha256"
}
```

The example is explanatory, not an executable submission. Blob metadata is
not authentication. Runtime `directUpload.trustedUploader` is the configured
`tenantId:objectId` integration identity and `allowedSources` must contain the
test source IDs. RBAC on incoming/ready is the trusted integration boundary.
No fax bytes, patients or manifest content enter Durable history.

## Live runner prerequisites

Fill `e2e/runner.example.json` with real private Blob/Function URLs, the runner MI
client ID, Entra API audience `api://<application-id>`, deployed versioned model
IDs, deployment artifact version, and independently recorded calibration ID.
Legacy `readerClientId` is accepted for existing configurations; if both keys
are present they must agree. An explicit observer different from the uploader
is mandatory; there is no uploader fallback for output reads.
The executable path is `evaluate_development.py --execute` after promotion,
then `configure_runtime.py --development-evidence <passed-output>`.
The six-document development command produces the genuine scoped evidence ID;
an operator-written placeholder cannot enable configuration through that CLI.
Pin `runtimeConfigSha256` to SHA256 of UTF-8 sorted compact canonical JSON of the
actual runtime configuration (`sort_keys=True, separators=(',', ':')`).

The `runnerClientId` uploader MI requires incoming/ready writes. Set mandatory
`observerClientId` to the author/evidence-reader MI with data Blob read access
(including incoming, ready, jobs, working, extracted, routed, human-review).
It must differ from the uploader; missing observer configuration is rejected.
Review release additionally needs API app role `Fax.Review`.
Set optional `reviewerClientId` to a distinct reviewer MI assigned to the runner
to separate Blob upload from review authorization. When omitted, review uses
`runnerClientId`. Give `Fax.Review` only to the selected review identity.
Private DNS/networking must reach Blob and the private Function endpoint.
`deniedClientId` is the existing allowlisted uploader MI with `Fax.Submit` but
without `Fax.Review`, distinct from the reviewer (not a new unallowlisted MI).
This tests application-role denial rather than platform-client rejection. No user impersonation
headers or function keys are manufactured.

The source labels are trusted integration labels `clinic-a`/`clinic-b`, not
cryptographic evidence of a caller or real clinic. The runner's five symbolic
profile keys below map to those labels in `runner.json`; they are not five new
source identities. Use separately pinned runtime configuration variants and
fresh evidence directories when two profiles require conflicting behavior for
the same source/type. Select only the cases matching the deployed variant:

* `one`: exactly one route per type; valid demo HL7 profile.
* `none`: zero matching routes; still complete extracted output.
* `multi`: exactly two matching routes; valid demo HL7.
* `hl7-not_applicable`: explicit `{"status":"NotApplicable","reason":"TEST ONLY ..."}`
  mapping for exam, one matching route.
* `hl7-error`: explicit exam mapping with a 64-character sending-application
  namespace, accepted by configuration policy but rejected by the actual strict
  HL7 parser (`hl7_profile_validation_failed`). No mapping is omitted; a hold
  for some unrelated inference reason does not pass this case.

Routes match independently: avoid global document-type-only routes accidentally
matching your zero/multi source profiles. Valid profiles are
`demo-oru-r01-v1`, `demo-orm-o01-v1`, `demo-rde-o11-v1`, configured source systems,
processing ID `T`, and rx `doseUnits` (e.g. `TESTUNIT`). HL7 v2.5.1 ends as Blob
artifacts; there is no EHR receiver, ACK or clinical interoperability claim.

```powershell
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --dry-run
# Execute only after explicit authorization on the private Azure runner:
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --case exam-pdf-body --execute
```

Generated runner configurations include `selectedCases`, which is honored by
the direct CLI by default. For manual configurations, explicitly repeat `--case`
to select compatible cases. The infrastructure Live wrapper requires explicit
`-Cases`, optional explicit `-Protocols`, and the matching `-Variant`;
none of these switches changes the deployed configuration. With only
`clinic-a`/`clinic-b`, run route and HL7 variants as separate bounded subsets:

| Generated configuration | Compatible case subset |
|---|---|
| `happy` (normal/baseline deployment) | 18 cases with `sourceProfile: one` |
| `no-route` | `routes-none` |
| `multi-route` | `routes-multi` |
| `hl7-not-applicable` | `hl7-not-applicable` |
| `hl7-error` | `hl7-error` |

Deploy/restart each pinned variant before its subset and verify the deployed
configuration hash. Use a separate evidence directory per variant, but the
**same** directory for that variant's cases and protocols; snapshot aggregate
summaries before another invocation replaces them. In the baseline subset,
include `exam-pdf-body` before duplicate/conflict and `missing-required` before
denied-review. Mutating review protocols create independent prerequisite faxes
themselves; do not separately seed them with `run_case`.
Do not claim the full suite passed against a configuration that cannot express
its conflicting source/profile expectations. Repeat `--case` for a subset.
Persistent attempt cap is 30; poll deadline defaults to 900 seconds/case,
maximum 1800. The same state reuses fax IDs rather than inventing another
submission after an ambiguous failure. One process owns each evidence directory.
Every selected case emits JSON plus aggregate JUnit. Skipped, pending or
inconclusive work is **never** represented as passed and produces nonzero exit.
Keep the per-case JSON and journal; successive protocol commands replace the
latest aggregate view, not the per-case evidence files.

## Coverage and interpretation

| Layer | Coverage / limitations |
|---|---|
| Real held-out inference | exam/order/rx × PDF/TIFF × cover/no-cover; actual normalized fields, body page count, artifact hashes, manifest/config status and HL7 type/test/version |
| Routing/config integration | zero/one/two routes; HL7 NotApplicable/error source configuration |
| Ambiguity/error probes | missing PatientId, unknown inventory, mixed exam/order, cover-only, structurally invalid PDF/TIFF |
| Authorized review | corrected values from manifest reviewCorrection, explicit body pages, ETag-protected release, revision manifest/artifacts |
| Auth/concurrency protocols | denied reviewer MI, simultaneous same-ETag decisions, stale ETag rejection |
| Deterministic backend tests | exact confidence boundaries (.7999/.8), service failures, duplicate-trigger/fan-out recovery, adversarial admission behavior; run separately under app/tests |

A three-class model may confidently misclassify an unknown or mixed fax. These
are **genuine adversarial holdout assertions**, not promises that noise creates
a particular score. Failures must remain failures; do not replace inference
with manifest/filename lookups. The two structurally invalid PDF/TIFF fixtures have valid magic headers and
should reach `PendingHumanReview` with reason `unreadable_document`; this is
distinct from rejecting the initial admission. The `admission-rejection`
protocol uploads a new original followed by a marker with a deliberately wrong
SHA256. It requires a positive immutable receipt at
`jobs/_admission-rejections/{faxId}/{sha256(rawMarker)}.json` with status
`RejectedAttempt` and reason `original_digest_mismatch`, and verifies the pinned
configuration/digest/byte scope. That proves one rejected attempt, **not**
terminal job fate or the outcome of subsequent retries. No receipt before the
deadline is inconclusive, never passed. Missing originals are retriable storage
failures and do not necessarily create such receipts.

To exercise protocols after their base case has run:

```powershell
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --protocol duplicate --execute
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --protocol conflict --execute
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --protocol admission-rejection --execute
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --protocol denied-review --execute
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --protocol human-corrected-release --execute
python e2e\run.py --config runner.json --root e2e\generated\synthetic-v1 --evidence e2e\evidence\run1 --protocol concurrent-review --execute
```

Duplicate/conflict use the `exam-pdf-body` base case and verify Blob immutable
replay/conditional-create semantics, **not** forced duplicate-trigger execution.
Both mutating review protocols upload an isolated `missing-required` original
under a new persisted fax ID and verify its real review outcome before release.
They do not mutate the original held-out fax or share a fax with each other.
Seed submission and review together consume one protocol attempt: 22 cases plus
six protocols use 28 attempts, within cap 30, without separately calling
`run_case` for protocol seeds. Use the same evidence/state directory.
The six protocols add three one-page originals: two inferred review seeds plus
one intentionally rejected admission (no DI expected). Duplicate/conflict reuse
the existing original, and denied-review uploads nothing. Thus a full initial
run uploads 30 readable source pages, of which 29 may reach DI, plus two malformed
originals. Planned classifier/custom page-passes increase by four/two over the
held-out baseline; concurrency and human release do not rerun DI.
Simultaneous lease
contention can return 503; exactly one release and subsequent stale-ETag 409 are
required. Unmet prerequisites are skipped, never silently passed.

GET `/api/faxes/{faxId}/review` returns ETag. POST the same endpoint with If-Match
and exactly `{documentType,bodyPages,values,reason}`. Original evidence and model
scores remain immutable; human authorization does not raise confidence scores.
GET `/api/faxes/{faxId}/review/original` returns the immutable original attachment
using the reviewer API role, without granting that identity Blob Reader or SAS.
Positive review protocols verify its SHA256/MIME before submitting a correction;
negative-role protocols verify original download is denied. These MI protocols
prove service-principal authorization and release behavior, **not actual human
acceptance**. A real human session requires an approved delegated Entra client
and a documented person inspecting the original and authorizing corrections.
Reviewed completion is under `extracted/{faxId}/revisions/{revision}/manifest.json`;
automatic completion is `extracted/{faxId}/manifest.json`. Consumers must await
these completion manifests, never individual fan-out blobs.
E2E additionally requires `report.json` to be a hash-bound manifest artifact
with the matching completed status/mode; operational facts are not quality truth.
The immutable report is a `completion-prepared` snapshot: its
`completionPreparedAt` and phase checkpoints are not completion/SLA timestamps.
Follow its `completionClock` to the completion manifest's Blob `lastModified`,
or use terminal API facts `completedAt` with
`completedAtSource: manifestLastModified`. E2E `elapsedSeconds` is the runner's
observed wall duration (including polling), not service-only processing latency.
The runtime body-type classifier uses `splitMode=auto` to expose mixed documents
and accepts only one result covering the whole body. A confidently wrong result
can still fail independent qualification.

If a real failed review left an uncommitted reservation, use the audited
`POST /api/faxes/{faxId}/review/reservations/{revision}/abandon` recovery contract
with current If-Match and a reason before a new correction. It advances the ETag
and reserves a new revision path; never overwrite prior evidence. E2E does not
inject production failures to manufacture this condition.

## Release evidence, not clinical validation

Retain generator/version/manifest hashes, pinned model and deployed config
versions, each expected value, actual normalized value, route count, page
mapping, original/body/artifact SHA256, HL7 disposition/version and elapsed time.
Review JSON preserves exact corrected truth without altering the actual fixture.
Measure type/field accuracy and automatic/review rates on independently labeled
representative data before production consideration. Neither six training
samples nor 22 synthetic cases establish clinical acceptance or calibration.
