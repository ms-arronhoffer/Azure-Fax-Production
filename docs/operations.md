# Operations, acceptance and production release

## Readiness statement

The Python runtime implements fax-type classification, authorized human release,
and a shared canonical/HL7 completion barrier. Use the
[success criteria matrix](success-criteria.md) to qualify those stages and the
overall accuracy/reliability thresholds. The supplied HL7 profiles remain
synthetic demonstration profiles until approved for a real receiver.

This is a production-oriented implementation and deployment baseline, not a
certification of production readiness. Local tests and Bicep compilation cannot
prove your private DNS, Entra configuration, trained model quality, quotas,
Power Platform connectivity, recovery objectives or organizational compliance.

The production owner must sign off on the checklist below in the target
environment. No cloud deployment is performed by generating this package.

## Data lifecycle and consumer contract

| Location | Purpose | Consumer rule |
|---|---|---|
| `incoming` | Original upload and provenance | Preserve the original; not an accepted extraction |
| `working` | Normalized body and AI intermediate results | Restricted to the processing identity/operators |
| `jobs` | Job state, configuration/provenance and completion metadata | Authoritative job state, not a bucket listing |
| `extracted` | Primary accepted structured content and `{faxId}/manifest.json` commit marker | Consume only after the completed manifest exists; it is written after all required outputs |
| `routed` | Zero or more selected additional artifacts | Same completion rule; do not assume the first blob is the complete result |
| `human-review` | Rejected original and draft extraction/reasons | Not approved; restrict to designated reviewers |
| `fax-jobs-poison` queue | Messages exhausting queue retries | Operator investigation; never blind deletion |
| Host storage | Durable history/checkpoints, queues, tables and host files | Infrastructure, not a business export |

Blob Storage does not provide a transaction covering several output blobs.
During a failed or retried publication, some artifacts may exist before others.
Consumers must honor the successful terminal job status and artifact manifest.
They must not trigger irrevocable business actions merely on every BlobCreated.

Rejected extraction must not create accepted primary/routed artifacts. An
operational failure after an accepted read is different: it may leave partial
publication, which must not be consumed until completion.

## End-to-end acceptance matrix

Run from both the private runner and the real Power Automate connection.
Use synthetic data or approved sanitized faxes, never live confidential files
in console transcripts.

| Case | Expected outcome |
|---|---|
| All required field scores above 0.80 | Accepted primary and selected routed artifacts |
| Minimum field score exactly 0.80 | Accepted without rounding changes |
| One field at 0.7999, others at 0.99 | Pending human review; no normal outputs |
| Missing expected field | Pending human review |
| Field value present but confidence absent | Pending human review |
| Unreadable/invalid required field | Pending human review |
| Valid numeric zero / valid boolean false | Not mistaken for an absent value |
| Required repeated field missing/low in one row | Reject whole auto-read |
| No configured schema/model | Admission/preflight fails closed |
| Confident cover then body | Cover removed; original preserved; mapping correct |
| No cover | All body pages preserved in order |
| Cover-only fax | Review; do not publish an empty body as success |
| Ambiguous cover/body classification | Review; do not guess which pages to remove |
| Unsupported/mixed document type | Explicit unsupported/review behavior, not guessed extraction |
| PDF and multipage TIFF | Equivalent tested body interpretation |
| Corrupt/encrypted/oversize/excess-page file | Explicit bounded rejection; no uncontrolled resource use |
| Zero routing matches | Primary only |
| Two simultaneous matches | Primary plus two conditional artifacts |
| Retry identical idempotency key and body | Same logical fax; no duplicate business publication |
| Reuse key with different body/source | Conflict, not silent replacement |
| Enqueue/storage failure during intake | Client retry can recover without losing a claimed receipt |
| AI 429 / transient 5xx | Bounded retry/backoff; status remains truthful |
| AI permanent failure | Explicit terminal failure or review; no success-shaped fallback |
| Function restart while AI is pending | Work resumes according to persisted job/operation state |
| Restart during output publication | Idempotent artifacts; completion only after all writes |
| Exhausted queue retry | Poison handling and operator alert |
| Duplicate review action, if enabled | Audited/concurrency-safe, no duplicate release |
| Missing/invalid/expired token | Rejected by EasyAuth |
| Valid token without required app role | 403 |
| Caller querying another caller's fax | 403/404, not disclosure |
| Forged principal header from public internet | No access; public endpoint disabled and EasyAuth required |
| Outside-network request with valid token | No network access |
| Private API, SCM, Storage and DI DNS | Resolve and connect through expected private endpoints |
| Monitoring connectivity | Application telemetry arrives through approved private monitoring path |

Record actual request IDs, expected result, observed result, model/config version
and tester for each case. Use the [API contract](api.md) for actual routes and
payload formats. Where the API intentionally rejects a file before job creation,
there is no accepted upload receipt or human-review job.

## Alerting and response

Configure an operational owner and response channel before enabling admission.
Do not treat a deployed alert with no recipients as a functioning response plan.

Monitor:

- Function exceptions and elevated error rate, without logging extracted text.
- Queue depth and oldest-message age.
- Poison-message count and growth.
- Durable failed/stuck jobs and jobs with no terminal outcome.
- Document Intelligence throttling and request latency.
- Review backlog count and age.
- Failed/partial publication and source-specific rejection-rate changes.
- Private DNS/connectivity failures and token/role failures.
- Storage capacity and retention consumption.

The infrastructure guide distinguishes provisioned alerts from application
metrics that need a dashboard/query or additional instrumentation. Verify every
configured alert with a controlled failure; do not assume all listed metrics are
automatically emitted.

### Investigating a failed fax

1. Locate its `faxId` and job record. Do not paste original content into tickets.
2. Inspect state, safe error code, config/model version, and last update.
3. Determine whether this is **Rejected/PendingHumanReview**, an operational
   **Failed** job, or an in-progress job. Do not conflate these states.
4. Check private endpoint/DNS, role assignment propagation, service health,
   throttling and configured processing limits.
5. Inspect sensitive draft content only with the review/operator identity.
6. Fix the underlying cause. Follow supported reprocessing procedures in the
   API guide; retain a relationship to the original attempt.
7. Verify the completion manifest before downstream release.

Do not purge a Durable instance or delete queue messages to "unstick" work
without an approved recovery plan. Preserve evidence before destructive cleanup.

## Human review

Review storage deliberately separates rejected automatic reads from accepted
business outputs. Reviewers must check the original against the draft fields,
including the original page numbers and rejection reasons.

The packaged API implements governed review through
`GET/POST /api/faxes/{faxId}/review` and
`GET /api/faxes/{faxId}/review/original`. Rejected evidence is
`human-review/{faxId}/original` plus `review.json`, with `PendingHumanReview`
status. Follow [How to use](how-to-use.md#review-a-rejected-or-uncertain-fax)
and the complete [API contract](api.md#authorized-human-review).

- A stored confidence of 0.79 must remain 0.79 in the audit record.
- Human approval is an explicit, separately authorized action, not a change to
  model confidence.
- Keep actor, timestamp, reason, corrected values and original job linkage.
- Keep corrected/approved records distinguishable from automated acceptance.
- A reviewer role should not grant arbitrary writes to accepted output storage.
- Uploading or editing a review Blob does not invoke approval. Only the authorized
  API with its ETag/concurrency contract may release a reviewed fax.

## Retention, recovery and continuity

Decide actual retention periods with the data owner before deployment. They may
differ for originals, intermediate OCR content, reviews, accepted outputs,
telemetry and audit records.

- Versioning and soft deletion protect against some accidental changes; they
  are not by themselves a complete backup strategy.
- Intermediate data can be sensitive even when the source has been deleted.
- Purge completed Durable histories only after the required diagnostic window,
  and never while a job is running.
- Coordinate lifecycle policies with the maximum retry/review window.
- Preserve model/config versions long enough to reproduce an audited decision.
- Test restoring an accidentally deleted artifact and restoring application
  code/config. Measure actual recovery time.

This package is a **single-region** baseline. Zonal redundancy, where enabled
and supported, is not regional disaster recovery. Define RTO/RPO and, if
required, add a second-region plan: supported storage replication/failover,
Document Intelligence model copies, private DNS routing, and a duplicate-safe
admission cutover. Do not start two independent processors on the same source
without a documented ownership/fencing scheme.

## Security and release gates

Before handling real production data:

- [ ] Threat model, privacy review, data classification and retention approved.
- [ ] Entra role assignments and source/caller mappings tested.
- [ ] Public data-plane endpoints verified disabled, including SCM.
- [ ] Private Power Platform connector proven end to end.
- [ ] Runtime has no model-authoring or broad subscription permissions.
- [ ] App settings, logs and deployment artifacts inspected for secrets.
- [ ] Dependency inventory and vulnerability scan reviewed.
- [ ] PDF/TIFF parser limits and adversarial inputs tested.
- [ ] Malware screening decision approved. **This package does not itself
      certify uploaded files as malware-free.** Add an approved scanning
      quarantine/release control if required by policy.
- [ ] Document Intelligence models pass held-out business quality criteria.
- [ ] Confidence gate and no-bypass tests pass.
- [ ] Expected sustained/burst volume load-tested against allocated quotas.
- [ ] Private build provenance and package hash recorded.
- [ ] Alert recipients, review operators, runbooks and on-call ownership set.
- [ ] Recovery, rollback and partial-publication behavior exercised.
- [ ] Infrastructure what-if and cost review approved.

Production readiness is the result of these verified controls and operating
procedures, not a label attached to generated code.
