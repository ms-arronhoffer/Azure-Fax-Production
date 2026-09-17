# Fax processing success criteria and release gates

The thresholds are proposed release gates. A live synthetic reference result is
recorded below; it does not replace customer qualification. Clinical, data,
interface, security and operations owners must approve the targets.
All mandatory criteria must pass; a high score in one category cannot offset a
failure in another. Insufficient evidence means **not qualified**, not a pass.

The third page of the [drawing](../architecture/Azure-Fax-Production.drawio)
contains the visual matrix. The [workflow](../architecture/workflow.svg) shows the
individual-fax gates. These population-level criteria complement, but do not
replace, the fixed **every-required-field confidence >= 0.80** automatic gate.
Model confidence is not measured accuracy.

The [reporting metrics specification](reporting-metrics.md) and drawing page 4
translate these criteria into operational dashboards, with cohort definitions,
denominators, breakdowns and alert ownership. Reporting does not change the
qualification thresholds below.

## Implementation boundary

Python and Bicep implement page classification, whole-fax type classification,
document-specific extraction, reviewed release, demonstration HL7 2.5.1 profiles,
manifest-last publication, Event Grid admission and a recovery queue. The supplied
HL7 profiles are synthetic test profiles, not receiver-approved clinical interfaces.

## Live synthetic result (2026-09-16)

| Gate | Result | Release disposition |
|---|---|---|
| Event Grid admission | Matched and delivered; immutable original admitted; Durable orchestration started directly | Pass for the isolated reference |
| Happy-path smoke | PDF exam completed with exact canonical fields, HL7, routed artifacts and manifest-last commit | Pass |
| Configuration variants | No-route, multi-route, HL7 NotApplicable and fail-closed HL7-error cohorts all passed | Pass |
| Recovery/security protocols | Duplicate, immutable conflict, positive admission rejection, reviewed correction, denied reviewer and concurrent-review protocols all passed | Pass |
| Development model evaluation | 6/6 development faxes passed; 18/18 model operations succeeded | Pass for development only |
| Broad held-out exact-field matrix | 12/18 faxes passed all assertions (66.7%); six contained confidently misread identifier/name characters | **Fail** criteria C/D; do not release current models |
| Local regression suite | 85 tests and 112 subtests passed; E2E runner suite 23 passed | Pass |

The held-out misses included `O`/`0` and spacing substitutions despite field
confidence above 0.80. This proves confidence is not accuracy. Do not add broad
text rewriting to conceal those errors. Retrain with more representative scans,
add approved field-format/domain validation where the business contract supports
it, and rerun the required independent sample sizes. Production admission remains
disabled until all mandatory rows below pass.

### Separate classification stages

1. **Classify each page:** identify cover/body, require an unambiguous assignment
   for each page, preserve page mapping and remove only confidently identified
   covers. With no cover, retain all body pages.
2. **Classify type:** classify the logical fax from body evidence as exam record,
   order, Rx or another approved class. Record type, model version, confidence
   and evidence. Use a separately calibrated type threshold, initially proposed
   at >= 0.80; passing it never overrides the field-confidence gate.
3. Select the type-specific extractor, required-field schema and output mapping
   policy. Unknown, low-confidence, conflicting or mixed-type faxes go to review.
   Do not guess one type for an unsupported bundle. Segmentation of mixed faxes
   requires a separately approved design and per-document qualification.
4. A reviewer can confirm/correct the type and page plan, trigger the appropriate
   extraction and verify every required value against the original.

### Shared transformation barrier

Both automatically approved and human-approved data enter the same canonical
data contract and versioned output plan. Human approval must be audited with
reviewer identity, timestamp, evidence, corrected values and concurrency control.
Preserve machine scores; never increase them to turn a rejected read into an
automatic acceptance. Human approval is a distinct provenance mode, not another
way to pass the machine-confidence test.

For each destination and fax type, the output plan explicitly specifies whether
HL7 is required, which fields are selected, and the approved mapping/profile
version. If required, generate and validate **every** planned message before any
successful completion. Missing mapping, unresolved required value, unsupported
code or failed validation is an error, never a reason to mark HL7 NotApplicable.
Only an explicit versioned policy can select NotApplicable.

Agree the HL7 family, version, message/event profile and receiver requirements
before implementation. For example, exam data may require a result/document
profile, orders an order profile, and Rx a pharmacy-specific profile. These
are **not interchangeable** and this design does not prescribe a specific
ORM/ORU/Rx message or assert FHIR equivalence.

Validate syntax, segments/cardinality, required fields, identifiers and assigning
authorities, code systems, units, timezone handling, repetitions and delimiter
escaping. Reconcile selected values back to the approved source. Do not infer
clinical units or silently truncate data. Use deterministic message identities
scoped to fax, approved revision, destination and profile for retry safety.

Transformation or validation failure holds the fax and its restricted drafts;
bounded retries handle transient faults. Data problems return to review and then
repeat the transformation checks. Mapping defects require an approved fix and
regression tests. Human approval cannot override failed HL7 validation.

Write primary JSON and all planned routed JSON/HL7 artifacts, verify their hashes,
then commit the completion manifest **last**. Include approval mode, original
scores, approved revision, type/extraction models, schema and mapping versions,
HL7 validation evidence or explicit NotApplicable, and every artifact hash.
Only then report Completed. PendingHumanReview, TransformFailed and rejected
cases are not successful completion. A closed/rejected case may have a separate
administrative terminal status, but must not count as a successful processed fax.

Completion here means durable **blob publication**, not clinical delivery.
If delivery is later added, define receiver ACK/NACK, retries and reconciliation
as a distinct delivery state; blob persistence alone must not imply acceptance.

## Proposed success criteria matrix

All rows below are proposed mandatory release criteria. Agree throughput,
supported document envelope and working-day calendar before testing.

| ID | Criterion and denominator | Proposed threshold | Evidence / failure action |
|---|---|---|---|
| A | Fax-type classification on all independently labeled held-out supported faxes; missing/abstained supported predictions count as misses | Macro-F1 >= 0.98; recall >= 0.97 for **each** supported type; unknown/mixed detection recall >= 0.99 on a separate negative set | At least 200 faxes per supported type and 200 unknown/mixed samples; publish confusion matrix and counts. Failed classes remain review-only. |
| B | Cover removal precision = correctly removed cover pages / all removed pages; recall = correctly removed cover pages / all true covers | Precision >= 0.999; recall >= 0.99; **zero clinically relevant body pages removed** | Include no-cover, repeated/blank covers, multi-page records and poor scans; at least 1,000 true cover pages and 1,000 true body pages. Any harmful deletion blocks release. |
| C | Automatic all-required-fields-correct fax rate = audited automatic releases with correct type, page plan and every required field/item / all audited automatic releases | One-sided 95% lower confidence bound >= 0.99 | Independent adjudicated audit, separately for each enabled type/source stratum. Extra-correct fields cannot compensate for one required error. |
| D | Critical clinical released-data error rate = audited released faxes with at least one critical error / audited released faxes | **Zero observed critical errors** AND one-sided 95% upper bound <= 0.001 | At least 2,995 independent audited releases with zero errors for each claimed population. Assess automatic and human paths separately; a pooled sample cannot qualify individual strata. Any critical error holds affected releases and blocks qualification. |
| E | Human-reviewed all-required-fields-correct fax rate; same fax-level definition as C, after human approval and transformation | One-sided 95% lower bound >= 0.99; 100% reviewer identity/time/evidence audit coverage | Independent second audit, not reviewer self-scoring. Apply D to this path too. Missing audit provenance blocks release. |
| F | Fail-closed and authorization invariants across the enumerated negative/fault-injection test set | 100% expected blocking behavior; zero unauthorized or duplicate releases | Test absent/<0.80/nonfinite scores, invalid fields, unknown types, incomplete reviews, invalid required HL7, partial writes and replays. Human approval must not bypass HL7. |
| G | HL7 conformance and source fidelity for all required messages on adjudicated golden faxes across every destination/profile/version | 100% parse/profile conformance and selected-field reconciliation; zero critical identifier/code/unit mismatches | At least 200 golden faxes per mapping/profile with repeats, escaping, missing data and boundary values. Negative messages must be held. Apply fax-level D as well; parser success alone is insufficient. |
| H | Publication success within 15 minutes / all eligible jobs in the test cohort | >= 99.9%; 100% completed-manifest/artifact-hash reconciliation; zero lost accepted originals or duplicate releases in fault tests | Define eligibility before execution from supported valid input and approved data requirements. Include transform, dependency, retry and publication failures in the denominator. Minimum 10,000 eligible jobs at agreed peak load, including fault/restart scenarios. |
| I | Automatic end-to-end latency and review resolution SLA | Auto p95 <= 5 minutes; >= 95% pending-review cases resolved within 1 working day | Clock from persisted upload to manifest for automatic jobs; never omit failed/timed-out jobs (treat as over-SLA). Review resolution includes audited rejection but is **not** publication success. Report unresolved age/backlog. |
| J | Straight-through processing yield = automatically completed releases / all valid supported input faxes | >= 70%, proposed business-efficiency target | Include low-confidence/rejected reads in denominator; report by type/source and file quality. Missing safety/quality gates cannot be waived to hit yield; never lower 0.80. |
| K | Sustained qualification, drift and change control | >= 30-day pilot at agreed peak load; all other gates remain met; versioned evidence complete | Monitor rolling cohorts and separately audit automatic/human releases. A critical error immediately holds affected automatic releases for investigation; any mandatory miss or inadequate sample prevents qualification. |
| L | Final operational/clinical/interface acceptance | All mandatory gates signed off, with no unmeasured criteria marked passed | Named clinical, data, interface, security and operations owners approve evidence. Requalify after model/schema/mapping/review-workflow changes. |

Critical errors must be defined before testing, with clinical-owner adjudication.
Examples include wrong patient/encounter, wrong drug/dose/unit, wrong order or
result meaning, lost clinically relevant pages, and incorrect destination.
A critical error counts once at fax level even if several fields are affected.

For H, automatic clocks start at persisted upload; reviewed-publication clocks
start at audited human approval. Report these populations separately. Human
waiting time is excluded only from reviewed *compute/publication* latency and is
still measured in I. A predeclared invalid-input challenge set is not part of H
but must pass F; do not relabel technical failures as invalid inputs afterward.

## Statistical evidence and sampling

- Freeze model IDs, thresholds, schemas, mappings and code/review versions.
  Separate train, validation and test sets by patient/document/source/time as
  appropriate to avoid leakage. Do not tune against the final holdout.
- Use independently labeled ground truth with adjudication of disagreements.
  Include exam records, orders, Rx, each onboarded source, legibility levels,
  cover/no-cover and low-confidence/unknown/mixed examples.
- Report counts, point estimates and **one-sided 95% exact binomial
  (Clopper-Pearson) bounds** for correctness/error rates. F1 is not a binomial
  rate; report the confusion matrix and a stratified bootstrap interval for F1.
- For zero errors in `n` independent audited faxes:
  `error_upper_95 = 1 - 0.05^(1/n)`. At `n=2,995` this is below 0.001.
  For all correct, `correctness_lower_95 = 0.05^(1/n)`; at `n=299` it exceeds
  0.99. D is the larger sample requirement and still requires zero critical errors.
  With nonzero noncritical errors, calculate exact bounds rather than using
  these zero-error shortcuts.
- Repeated copies of the same fax in load tests do not increase the independent
  accuracy sample size. Handle clustering by patient/source in sampling and
  uncertainty analysis. Meeting a formula with dependent copies is not evidence.
- A pooled result does not establish per-type/source or per-approval-path
  performance. Gather adequate evidence for each claim; rare unsupported or
  under-sampled strata remain unqualified. The listed intervals are per claim;
  predeclare a multiplicity adjustment if making a simultaneous all-strata
  confidence claim.
- Distinguish sensitivity/recall from precision and distinguish yield from
  accuracy. Sending everything to review can improve automatic precision while
  failing J; auto-accepting everything can improve J while failing C/D/F.

## Qualification record template

| Version / cohort / dates | Criterion | Type / source / approval path | Numerator / denominator | Estimate / bound | Evidence link | Pass / fail / insufficient evidence | Owner |
|---|---|---|---|---|---|---|---|
| Not yet tested | A-L | Not yet tested | Not measured | Not measured | Pending | Insufficient evidence | To assign |

The existing local Python tests do not measure these proposed thresholds and do
not exercise the new target-only stages. Retain the current
[validation record](validation.md) as evidence only for what was actually run.
