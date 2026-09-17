# Validation record

## Verification boundary

An isolated synthetic environment was deployed in subscription
`79398e05-f318-4010-a508-d913c3763e39` in `rg-fax-e2e-westus2` and
`rg-fax-authoring-e2e-westus2`. No production fax was processed and no Power
Platform solution was imported. The infrastructure/workflow evidence below is
real Azure evidence; the current model accuracy is **not production-qualified**.

## Live Azure checks (2026-09-16)

| Check | Result |
|---|---|
| Private infrastructure | 11 private endpoint DNS targets verified |
| Function deployment | Python 3.11 Linux package indexed all 13 Functions |
| Event Grid | `ready-blob-created` matched and successfully delivered `BlobCreated` to `ready_manifest`; Function access is default-deny with only `AzureEventGrid` allowed |
| Event Grid durability | 30 attempts / 1,440-minute TTL and private `event-grid-deadletter` destination deployed |
| Direct Durable handoff | Immutable original admitted, orchestration intent persisted before `start_new`, Durable completed |
| Document Intelligence | 18/18 development operations succeeded; 6/6 development faxes passed |
| End-to-end smoke | Exam PDF completed with exact canonical data, HL7, routed artifacts and manifest-last commit |
| Policy variants | No-route, multi-route, HL7 NotApplicable and fail-closed HL7-error each passed |
| Recovery/authorization | Duplicate, conflict, positive admission rejection, reviewed correction, denied reviewer and concurrent review passed |
| Held-out exact-field suite | 12/18 passed all assertions; six had identifier/name OCR substitutions |
| Redeployment | Subscription deployment `fax-private-test` succeeded at 2026-09-16 21:33 UTC; both Azure Workbooks were created |
| Final bounded live rerun | 2/2 compatible fax cases and 6/6 duplicate/conflict/review/security protocols passed; no pending jobs |
| Admission after redeployment | `PROCESSING_ENABLED=true` for synthetic test documents only (2026-09-16) |

The 12/18 result fails the release target. The misses included confidently scored
`O`/`0` and spacing substitutions, demonstrating why confidence is not accuracy.
The deployed reference proves infrastructure and workflow integration, not
clinical reliability. Retrain and rerun the independent sample sizes in
[success-criteria.md](success-criteria.md) before production admission.

## Local checks

- `scripts\Validate-Package.ps1`: **86 application tests passed**, plus Bicep
  compilation and editable architecture validation.
- `app\.venv\Scripts\python.exe -m pytest e2e\test_local.py -q`: **23 passed**.
- `python -m pip check`: no broken requirements.
- Tests include mixed-type result rejection, typed canonical values,
  review concurrency/reservation recovery, manifest-bound report writes and
  Event Grid envelope validation/direct Durable start.
- The current `e2e/generated/synthetic-v3` corpus contains **86 assets and 22
  held-out cases**.
  Its manifest SHA256 is
  `0cb1a1283f49ebba555b3d20c47b43df25f2b80ac289f9eb2fc1f4d5e44d6297`.
  The complete synthetic-only archive has 114 entries and no PEM/virtualenv/cache
  files. The separate development evaluator uses REST `split`, not the SDK
  option name `splitMode`; a regression test verifies all 18 request options.

The bounded source archive contains no PEM files, virtual environments, installed
dependencies, caches or generated faxes. Runtime dependency PEM trust bundles
remain only inside the Linux-built deployment ZIP.

### Reporting-page revision

- Added page 4, **Reporting metrics and operational insights**, with 12 metric
  groups, breakdowns, data sources, cadence and action guidance.
- The editable draw.io file and SVG/HTML preview contain four pages with embedded
  Azure icons and the Event Grid primary path.
- All four SVGs rendered locally with no text overflowing node boundaries; the
  reporting page was visually inspected.
- [Metric definitions](reporting-metrics.md) document cohort/denominator rules,
  incomplete-job handling, privacy and ownership. No live dashboard, metrics
  collection, reporting pipeline or alert rules were implemented or deployed.
- Earlier validation results below are historical snapshots, not measurements
  of the proposed reporting or clinical-quality targets.

### Architecture revision

The four-page architecture shows Event Grid primary admission, queue recovery,
separate page/type classification, shared automatic/reviewed HL7 barriers,
success criteria and reporting metrics. The implementation now follows that path.

### Baseline implementation checks (before the architecture revision)

| Check | Recorded result |
|---|---|
| Original two-page Azure-icon diagram structure | Passed at baseline: 188 cells, 25 connected edges, 78 embedded-image checks; superseded by revision above |
| Embedded icon provenance | Selected official Microsoft SVGs preserved with source/hash manifest |
| Diagram visual preview | Both SVG pages rendered locally; workflow visually inspected |
| Diagram regeneration | Deterministic regeneration validated |
| Power Platform connector JSON | Parsed; binary upload, status route, OAuth and idempotency headers checked |
| Validation helper PowerShell parsing | Passed |
| Guide code-fence structure | Passed |
| Python unit tests | Passed: 43 tests on isolated Python 3.11.9 |
| Function SDK indexing / dependencies | All seven Functions indexed; `pip check` reports no broken requirements |
| Final documentation consistency | 12 Markdown documents, 44 local links and code fences checked; upload/status connector contract aligned with implementation |
| Bicep compilation / deployment helpers | Bicep and example parameters compiled; lint, PowerShell parse and offline safety/security-shape checks passed; no ARM deployment performed |
| DI authorization scope | Broad built-in role replaced by six-action custom inference/metadata role; local role scope/dependency checks passed; live allowed/denied calls remain required |

Final integration tests cover the exact 0.80 boundary, every-field rather than
average acceptance, missing/invalid fields, minimum-confidence summaries,
field rejection reasons, cover/no-cover processing, mixed-body rejection,
conditional fan-out, immutable publication and failure recovery. Additional
regressions verify ARM service URLs with a root slash, deterministic TIFF PDF
metadata, and poison duplicates not failing active or unconfirmed jobs.

The test suite uses in-memory Storage and mocked service responses. Function
indexing checks the Python SDK decorators, not an actual Functions host.
Expected fault-injection tests emit sanitized error/warning codes.

Reproduce the local checks with the prepared Python 3.11 environment:

```powershell
.\scripts\Validate-Package.ps1 -PythonExecutable "$PWD\app\.venv\Scripts\python.exe"
.\scripts\Deploy.ps1 -Mode Build
az bicep lint --file .\infra\main.bicep
.\architecture\validate-diagrams.ps1
```

The final Bicep lint produced no code diagnostics. A compiled-template check
confirmed the custom role's six DataActions and its inferred
definition-before-assignment dependency.

The connector definition still requires tenant, API audience/scope and Function
host substitution. JSON parsing is not a successful Power Platform import test.

## Not proven by local checks

- Region/SKU capacity, subscription policy, budgets or role-assignment authority.
- Existing subnet delegation/capacity and private DNS/route configuration.
- Power Platform connector's private connectivity from the actual environment.
- EasyAuth token issuer/audience/client/role behavior in the target tenant.
- Runtime managed-identity access and role propagation.
- Private SCM build/deployment and Linux Function host startup.
- Azure Monitor Private Link Scope ingestion/query connectivity.
- Model training/copy support under actual authoring/runtime network policies.
- Real model IDs, field schemas, held-out extraction accuracy or confidence
  calibration.
- End-to-end Durable replay, poison queue, throttling and partial-publication
  recovery under live service failures.
- Throughput, review backlog, recovery objectives or compliance controls.

Do not interpret compilation or unit-test success as evidence for those
properties. Record target-specific results and sign-off before enabling live
admission.
