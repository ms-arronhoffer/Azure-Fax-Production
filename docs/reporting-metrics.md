# Reporting metrics and dashboard specification

Two reusable Azure Workbooks now implement the currently available operational
and model-signal views. They are not measured clinical-quality results.
Page 4 of the [editable drawing](../architecture/Azure-Fax-Production.drawio)
and the [reporting preview](../architecture/reporting.svg) summarize the
high-value metrics below. The [success criteria](success-criteria.md) define
qualification thresholds and statistical evidence; reporting does not replace
those gates.

## Deployed workbook definitions

The reusable Bicep deploys:

- **Fax Processing - Operations:** request volume/failures/latency, latest workflow
  outcomes, review backlog, completion latency and safe failure codes.
- **Fax Processing - Quality and Release:** outcomes by source/type, rejection
  drivers, confidence bands, HL7 disposition and active model/configuration
  versions, with an explicit release-evidence warning.

Definitions are in `infra/workbooks/` and are deployed by
`infra/modules/workbooks.bicep`. Open them from **Azure Monitor > Workbooks** or
from the Application Insights resource's Workbooks blade. Select the shared
workbook with the applicable display name.

Workbook readers need management-plane permission to read the Workbook and query
permission on the linked workspace/Application Insights resource. Grant the
least-privileged organization-approved reader role at the narrowest scope; do not
grant Storage data access merely to view aggregates.

The application emits `FAX_METRIC` traces only after the corresponding immutable
safe `report.json` write succeeds. The payload uses the same bounded reporting
contract and excludes fax text, extracted values, HL7 content, reviewer reasons,
tokens and Blob URLs. Workbook queries deduplicate retries by fax ID and latest
fact. Application Insights sampling still means these are operational views, not
the authoritative business ledger.

## Dashboard views and common breakdowns

| Audience / cadence | High-value view | Decision supported |
|---|---|---|
| Executive / weekly | Input and completed volumes, straight-through yield, audited correctness, critical incidents, SLA attainment, unit cost | Whether the process is safe, effective and worth expanding |
| Operations / 5 minutes to hourly | Arrival/completion balance, oldest unfinished job, review backlog, stage latency, HL7 failures, retries/poison | Where to intervene, staff or increase capacity |
| Clinical quality / weekly and each release | Type/cover confusion, required-field rejection drivers, independent auto/human accuracy, critical errors and evidence bounds | Whether a model, source or mapping remains qualified |
| Governance / monthly | Provenance, access-control violations, audit coverage, costs, version changes and requalification status | Compliance review, financial reconciliation and release authorization |

Every tile should show **numerator, denominator, cohort/window, refreshed-at time,
trend, target, owner and evidence status**. Support these shared filters:

- Environment and reporting period; retain UTC timestamps and explicitly label
  any local display timezone.
- Confirmed fax type: exam record, order, Rx, approved other, unknown or mixed.
  Keep predicted and adjudicated type distinct for classification reporting.
- Approved source identifier, format, page-count band and scan-quality band.
  Source identifiers must be governed codes, not patient names or raw fax numbers.
- Automatic versus human approval, processing stage and safe reason code.
- Page/type/extraction model, required-field schema, mapping/profile and deployed
  code version; route/destination for artifact-level metrics.
- Review team, not individual reviewer names, in general dashboards. Person-level
  audit evidence belongs in a separate authorized case/audit system.

## Metric dictionary

References A-L point to the success criteria matrix. Targets here are carried
forward as **proposed**; do not imply achieved performance.

| ID / group | Metric definition | Breakouts / authoritative inputs | Cadence / target and action |
|---|---|---|---|
| R1 Demand and throughput | Distinct persisted intake faxes and pages; distinct faxes with a valid completed manifest per hour; arrivals minus departures and unresolved stock | Type, source, format, pages; intake/job ledger plus manifests. Report invalid pre-persistence requests separately as requests, not fax jobs. | 5-minute operational trend, daily capacity. Investigate sustained arrival/completion imbalance; capacity threshold requires agreed workload. |
| R2 Funnel and yield [J] | Stage-entry and pass counts for the same intake cohort. Straight-through yield = distinct automatically completed faxes / all valid supported input faxes. Show human-completed, pending/held, technical failures and closed-rejected separately. | Type/source/version/approval; deduplicated transitions and manifests | Daily mature-cohort report; proposed yield >=70%. Never remove rejected reads from denominator or lower the 0.80 gate. |
| R3 Audited quality [C,D,E] | All-required-fields-correct fax rate = independently audited releases with correct type/page plan and every required value/item / all audited releases. Critical-error rate = audited faxes with >=1 critical error / audited faxes. | Auto/human separately, type/source/model; independently adjudicated audit joined to release revision | Weekly / rolling 30 days; correctness lower one-sided 95% bound >=99%; zero observed critical errors and upper bound <=0.1%. Any critical error is an immediate safety incident. |
| R4 Classification and covers [A,B] | Supported-type confusion matrix, macro-F1 and per-type recall. Unknown/mixed referral rate on all classified faxes is separate from detection recall on labeled unknown/mixed examples. Cover precision/recall require true page labels. | Predicted vs adjudicated type, source, quality, model; decisions plus labeled audits | Weekly / each model change. Compare with A/B; zero harmful body-page removal. High referral rate alone is not proof of accurate unknown detection. |
| R5 Field rejection drivers [F] | Any-field-gate rejection rate = faxes failing / faxes evaluated at that gate. For each required field path: faxes missing it / evaluated faxes whose schema requires it. Also count unreadable, invalid and below-threshold reasons and minimum-confidence bands. | Field schema IDs/type/source/version; gate result codes, no values | Daily Pareto chart. A fax can have several reasons; reason percentages can exceed 100%. Distinguish fax-level from array-item-level counts. Use for labeling/schema improvements, not confidence inflation. |
| R6 Review operations [E,I] | Pending case count and oldest age; waiting-to-first-action time; active handling time (only if explicitly instrumented); resolution SLA; rework = resolved cases reopened in a declared follow-up window / cases with full follow-up. | Type/reason/source/team; case transitions, audited decisions and independent quality audit | Hourly queue / daily SLA; >=95% resolved within one working day. Resolution can be approved or rejected and is not publication. Define calendar/timezone. |
| R7 HL7 [G] | First-pass fax conformance = required-HL7 approved revisions for which **all** planned messages pass their first validation / all revisions entering required-HL7 processing. Eventual conformance also includes retries. Separately measure selected-field fidelity against independent golden-source audits. | Mapping, profile/version, destination and approval mode; output plan, validator summaries, golden audits | 5-minute failures / daily trend. Required released HL7 conformance 100%. Missing mapping counts as failure, not N/A. First-pass improvement is diagnostic, not permission to release invalid messages. |
| R8 Reliability [F,H] | Completed-within-15-min eligible jobs / all eligible jobs whose deadline has elapsed. Manifest reconciliation = completed releases with every planned artifact present and hash-valid / all completed releases inspected. Also show lost originals, duplicate releases, retries, poison count and oldest unfinished age. | Stage/dependency/route/deployment; ledger, queue signals and reconciliation results | 5-minute operations / daily cohort SLO; >=99.9% deadline attainment, 100% reconciliation, zero loss/duplicates. Technical/required-HL7 failures remain in denominator. |
| R9 Latency [I] | Auto persisted-upload-to-manifest p50/p95/p99; reviewed approval-to-manifest separately; queue, classification, extraction, transformation and write durations. Also report overdue/failed share for each deadline cohort. | Type, pages, format, load and approval; milestone timestamps | 5-minute operations / daily distribution; auto p95 <=5 min. Never report survivor-only percentiles as the all-job SLA. See unfinished-job rules below. |
| R10 Controls [F,L] | Unauthorized release/bypass incidents; missing approval/mapping provenance / inspected releases; access denials by reason. Distinguish blocked attempts from successful misuse. | Operation/reason/environment; access decisions and release-to-audit reconciliation | Immediate alert for verified unauthorized release/bypass; daily summary. Zero violations, 100% required audit coverage. A drop in denial counts does not establish stronger security. |
| R11 Cost/value | Allocated cloud cost / completed business faxes; cloud cost / input pages; review labor = measured active minutes x approved loaded rate. Show costs on failed/held work separately, not just successful work. Savings require a measured manual baseline. | Period/environment; type/source allocation only if supported; billing and review-time records | Daily provisional / monthly reconciled; Finance owns budgets. Shared Premium/storage/monitoring cost allocation is an estimate; do not imply exact per-fax billing. |
| R12 Drift/report trust [K,L] | Source/type/quality mix, confidence and rejection distribution vs frozen baseline; audit coverage = independently audited / released revisions in the cohort. Freshness = now minus source watermark; reconciliation gap = ledger facts not represented / expected ledger facts. | Version/source/type/approval; registry, unsampled ledger, telemetry, audit and reporting watermarks | Daily drift / weekly quality / monthly governance. Missing/stale data is Unknown. New sources, version changes and material drift trigger investigation and requalification, not automatic threshold lowering. |

## Counting and time-window rules

1. Business volume counts each `faxId` once. Publication evidence is keyed by
   fax + approved revision; routing and HL7 use revision + destination + message
   identity. Retry attempts and repeated event delivery must not multiply a fax
   count. For corrected/released revisions, retain all audit evidence but do not
   present each revision as a new incoming fax.
2. Use an **intake cohort** for yield and funnel measures. Do not divide today's
   completions of older jobs by today's uploads. Display cohort maturity and
   pending counts. Use elapsed-deadline cohorts for SLA reporting; never classify
   recently arrived work as already meeting a deadline.
3. Outcome categories are mutually exclusive at an as-of timestamp. Stage
   failures/reasons can overlap and must be labeled as such. Closed-rejected and
   PendingHumanReview are not successfully completed faxes.
4. Reliability eligibility is defined in advance as in H; technical failures,
   missing required mappings and required-HL7 validation failures cannot be
   reclassified out of the denominator after execution.
5. For auto latency include failed and overdue-unfinished jobs as over-SLA in
   deadline reporting. Show observed completion percentiles separately; for an
   all-job percentile treat terminal failures as infinite duration and open jobs
   as right-censored at their current age. If censoring prevents determining p95,
   show **Not yet estimable** with overdue counts, not an invented number.
6. Review SLA uses the approved working-day calendar. Include unresolved cases
   whose SLA deadline elapsed as breaches; younger unresolved cases are pending.
   Report reviewed publication time from audited approval separately from human
   queue/wait/handling time. Do not infer active human work from wall-clock delay.
7. Accuracy comes from independent audits, not confidence scores, reviewer
   correction frequency, or HL7 parser success. Use the sample sizes/bounds in
   the [success criteria](success-criteria.md), show `n`, and distinguish a
   targeted diagnostic sample from representative population evidence.
8. Zero denominator, missing events, stale data and insufficient audit samples
   display **N/A / Unknown / insufficient evidence**, never 0% errors or a green
   pass. Use consistent mature windows and comparable source/type mix for trends.

## Proposed reporting inputs and privacy boundary

Use a reconciled job/manifest ledger for unsampled business totals, safe stage
telemetry for operational timings, a restricted independent audit source for
correctness, review-case records for human work, and billing for cost. A
read-only aggregation pipeline can join these into governed reporting tables
and role-scoped dashboard views. The two Workbooks are provisioned; the governed
aggregation/audit/billing store described here is not.

Required facts include pseudonymous join ID, event/revision ID, UTC milestone
time, approved source/type code, approval mode, stage/status, safe reason code,
versions, page count, planned-artifact counts and boolean validation/audit
outcomes. Deduplicate by stable event identity; handle late events using source
watermarks and recomputation of affected cohorts. Reconcile the report to job
and manifest truth and display reconciliation coverage and last successful run.

Do not put raw fax/OCR text, extracted clinical values, HL7 payloads, patient
identifiers, tokens, connection strings, blob URLs or freeform exception text in
dashboard facts. Pseudonymous IDs can still be sensitive: restrict row-level
drill-through, keep audit joins in governed access boundaries, minimize retention
and suppress small-group breakdowns according to organizational policy.
General reports use aggregates and approved low-cardinality dimension codes.

The application's sampled telemetry and safe facts do not provide this entire
dictionary. Billing, independent audits, receiver acknowledgments and authoritative
unsampled ledger reconciliation need separate governed inputs. Do not derive exact
business counts or clinical accuracy from sampled request/fact traces.

## Alert and ownership policy

- **Immediate incident:** any verified critical clinical error, harmful body-page
  removal, unauthorized release, failed-HL7 bypass, lost accepted original or
  duplicate release. Clinical/security/on-call owners hold affected releases and
  investigate; alert text must not include clinical content.
- **Operational warning:** deadline SLO deterioration, review SLA breach,
  increasing oldest-job age, sustained HL7 failures, retry/poison growth or stale
  report inputs. Operations defines consecutive-window rules, minimum cohort
  sizes and escalation times from the agreed workload before enabling alerts.
- **Quality warning:** statistically supported decline or materially changed
  source/model mix. Quality owners review stratum-specific audits and versions.
  No automatic threshold lowering or silent adoption of a new model/mapping.
- **Financial warning:** Finance approves cost allocation, budgets and cost
  variance thresholds. Do not invent a universal dollar-per-fax target.

Every alert has an owner, runbook, deduplication key, severity and resolution
evidence. Dashboard thresholds are proposed, not installed alert rules.
