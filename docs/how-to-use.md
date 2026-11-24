# How to use the fax-processing system

This guide is for submitters, reviewers, operators and output consumers after the
Azure environment, Entra roles, runtime configuration and models have been
deployed. For first-time deployment, use the [deployment guide](deployment.md).

> **Current reference-environment status (2026-09-16):** processing is enabled
> for synthetic test documents in `rg-fax-e2e-westus2`. The synthetic models
> are not approved for clinical production. Do not upload PHI or treat these
> results as clinically validated.

## Before processing a fax

Confirm all of the following:

1. The fax is a PDF or multipage TIFF within the configured 20 MiB and 100-page
   limits.
2. The source value (for example, `clinic-a`) is on the approved source list.
3. The submitting identity has the `Fax.Submit` app role and is mapped to that
   source.
4. `PROCESSING_ENABLED=true` only after the current model/configuration release
   has passed the [success criteria](success-criteria.md).
5. The submitter has a stable UUID idempotency key. Reuse it when retrying the
   same fax; never reuse it for different content.

## Option 1: process a fax from Power Apps

1. Open the approved fax-intake Power App.
2. Select one PDF or TIFF.
3. Select the fax source from the provided list. Do not enter a Blob path.
4. Select **Submit** once.
5. Save the returned fax ID. “Uploaded / Processing” means the system accepted
   the request; it does not mean extraction succeeded.
6. Leave the app open to poll status, or use the fax ID later to reopen status.
7. Follow the applicable terminal result:
   - **Completed:** processing and all configured outputs completed.
   - **PendingHumanReview:** a reviewer must inspect and correct/confirm the fax.
   - **Failed:** an operator must investigate; do not treat it as a review or a
     completed result.

The Power Platform administrator must first configure the private connector and
flow described in [Power Platform setup](power-platform.md).

## Option 2: process a fax through the HTTP API

Use this option for an approved system integration. Obtain an Entra access token
for the exact Fax API audience using the integration's approved OAuth flow. Do
not put tokens or fax bytes in logs.

Send the complete file as the binary request body:

```http
POST https://<function-app>.azurewebsites.net/api/faxes
Authorization: Bearer <access-token>
Content-Type: application/pdf
x-fax-source: clinic-a
idempotency-key: 57d1bd00-4501-41f9-b7d7-3fbd89fcdca8

<binary PDF bytes>
```

Use `image/tiff` for TIFF. Do not send JSON, base64 text, a multipart envelope,
a URL or a SAS token. A successful request returns HTTP 202, a `faxId`, a
relative `statusUrl`, `Location`, and `Retry-After: 10`.

If the request times out or returns a transient 503, retry the exact content,
source and original idempotency key. A new key creates a new logical fax. A 409
means the key was reused with different content/source/MIME and must be
investigated, not automatically retried.

### Run a submitter smoke test

From a machine that can reach the private Fax API, run this from the repository
root in PowerShell 7.2 or later. Use an approved synthetic fixture, never PHI.
The script prompts for an access token issued for the Fax API audience, using
an identity with `Fax.Submit` and permission for the chosen source.

```powershell
.\scripts\Test-FaxUserWorkflow.ps1 `
   -BaseUrl 'https://<function-app>.azurewebsites.net' `
   -FaxPath '.\synthetic-exam.pdf' -Source 'clinic-a'
```

The script submits one fax and checks that it reaches `Completed` with a
manifest pointer. For a fixture intended to be held, add
`-ExpectedStatus PendingHumanReview`. This checks the submitter experience,
not the reviewer interface or the contents of published artifacts. If submission
times out or returns a transient 503, reuse the displayed `-IdempotencyKey`
with the same identity, file, and source rather than creating another fax.

## Option 3: process a fax through the trusted Blob integration

Use this only for the dedicated managed identity allowed to write `incoming` and
`ready`; end users must not have direct Blob access.

1. Generate a lowercase UUID `faxId`.
2. Compute SHA-256 over the exact complete PDF/TIFF bytes.
3. Create, without overwrite, `incoming/<faxId>/original`.
4. After the original upload is fully committed, create, without overwrite,
   `ready/<faxId>.json`:

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
  "sha256": "<actual 64-character lower-case SHA-256>"
}
```

The immutable ready manifest is the commit marker. Event Grid validates the
event and starts Durable Functions directly. Do not write the ready manifest
first, overwrite either Blob, add fields, or place credentials in metadata.

## Check processing status

Poll no faster than the returned `Retry-After` value:

```http
GET https://<function-app>.azurewebsites.net/api/faxes/<faxId>
Authorization: Bearer <access-token>
```

The owner, `Fax.Admin`, or `Fax.Review` may read the safe status. Expected states:

| Status | Action |
|---|---|
| `Accepted` | Wait; immutable admission completed |
| `Processing` | Wait; classification/extraction is running |
| `Publishing` | Wait; never consume partial output Blobs |
| `PendingHumanReview` | Use the review procedure below |
| `Failed` | Follow [failed-fax operations](operations.md#investigating-a-failed-fax) |
| `Completed` | Read the committed manifest and verify every artifact hash |

For privacy, status does not return raw extracted values. Authorized operational
facts are available from `GET /api/faxes/<faxId>/facts`.

## Review a rejected or uncertain fax

The reviewer needs `Fax.Review`. Review actions are audited and do not increase
or replace the original machine confidence.

1. Get review evidence:

   ```http
   GET /api/faxes/<faxId>/review
   Authorization: Bearer <reviewer-token>
   ```

2. Save the response `ETag` (for example, `"review-0"`).
3. Download the immutable source with
   `GET /api/faxes/<faxId>/review/original`.
4. Inspect the source itself, page plan, fax type, every required value,
   confidence and rejection reason.
5. Submit the complete corrected decision with `If-Match` set to the saved ETag:

   ```http
   POST /api/faxes/<faxId>/review
   Authorization: Bearer <reviewer-token>
   If-Match: "review-0"
   Content-Type: application/json
   ```

   ```json
   {
     "documentType": "exam",
     "bodyPages": [2],
     "values": {
       "PatientId": "SYN-001",
       "PatientName": "Synthetic Person",
       "DateOfBirth": "1970-01-01",
       "EncounterId": "SYN-E-001",
       "DocumentDate": "2026-09-15",
       "ProviderId": "SYN-P-001",
       "ExamName": "Synthetic exam",
       "ResultText": "Synthetic result"
     },
     "reason": "Inspected the original and corrected all required values"
   }
   ```

6. If HTTP 409 is returned, fetch the review again and reconcile the newer
   revision. Never overwrite another reviewer's decision.
7. If the response is ambiguous, check status before resubmitting the same
   identity, ETag and body.

Human-reviewed data passes the same canonical, routing and HL7 validation as
automatic data. A reviewer cannot override invalid HL7 or an incomplete schema.

## Consume completed output

Do not consume output merely because a Blob appeared.

1. Wait for `Completed`.
2. Read the manifest pointer returned by status.
3. Read `extracted/<faxId>/manifest.json` for automatic completion, or the
   referenced immutable revision manifest for reviewed completion.
4. Verify every listed artifact SHA-256.
5. Consume `canonical.json`, `content.json`, optional `content.hl7`, and only the
   routed artifacts listed by the manifest.
6. Record `completionMode`, model/config/schema/mapping versions and review
   revision for traceability.

## Add a new fax schema or document type

Yes, the package supports this as a controlled model/configuration release:

1. Approve the new type, sources, required fields, types and repeating groups.
2. Define its canonical contract and receiver-approved HL7 profile, or an
   explicit approved `NotApplicable` policy.
3. Add independently labeled positive samples and confusing negative/mixed
   samples to the fax-type classifier.
4. Train a type-specific extractor and confirm its real `resultDocType`.
5. Add the type label, model ID, ordered required fields, routes and HL7 mapping
   to a new versioned runtime configuration.
6. Add positive, low-confidence, missing-field, invalid-value, PDF/TIFF,
   cover/no-cover, review, routing and HL7 failure tests.
7. Keep the type review-only until its own held-out cohort passes all release
   gates. Overall averages cannot qualify a weak document type.
8. Promote and deploy with processing disabled, verify bindings/models, run a
   bounded smoke cohort, then obtain release approval before enabling it.

See [Custom models — Add a new fax type and extraction schema](models.md#add-a-new-fax-type-and-extraction-schema)
for the detailed procedure.

## Retrain an existing model

Retrain when layouts/sources change, error or review rates drift, a field schema
changes, or independently labeled evaluation finds confident errors.

1. Adjudicate failures against the original fax; do not train on unverified OCR.
2. Add representative layouts, sources, scan quality, PDF/TIFF, cover/no-cover,
   and difficult characters to a new training version.
3. Keep patient/source episodes in one partition to avoid data leakage.
4. Train a new versioned model ID; never overwrite the qualified model in place.
5. Evaluate the complete fax-level outcome on an untouched held-out set.
6. Do not globally replace `O` with `0` or otherwise mutate ambiguous clinical
   identifiers. Use authoritative format/reference validation only when approved;
   route ambiguity to review.
7. Promote, deploy disabled, smoke-test, approve and enable a bounded cohort.
8. Retain the previous model/configuration for rollback and keep existing jobs
   tied to their configuration snapshot.

The detailed retraining, promotion and rollback process is in
[Custom models](models.md#correct-confident-ocr-substitutions-before-another-release-attempt).

## Quick troubleshooting

- **400/413:** validate MIME, file signature, UUID, size and page limits.
- **403/404:** verify network path, token audience/tenant, app role, source mapping
  and ownership; 404 may intentionally avoid disclosing another owner's fax.
- **409:** idempotency or review ETag conflict; fetch current state and reconcile.
- **422 on review:** correct the complete page/type/field contract or HL7 mapping.
- **503:** preserve the original idempotency key or review ETag/body and retry only
  after the service/configuration issue is corrected.
- **Stuck processing:** inspect the safe job state, Event Grid delivery/dead-letter,
  Durable status, private DNS/RBAC and Document Intelligence health. Do not delete
  queue messages or Durable history without preserving evidence and approving a
  recovery plan.

See [Operations](operations.md), [API contract](api.md), [RBAC](rbac.md), and
[Validation](validation.md) for detailed controls and current qualification status.

## View dashboards

1. In the Azure portal, open **Azure Monitor > Workbooks** or the deployed
   Application Insights resource.
2. Open **Fax Processing - Operations** for traffic, failures, latest outcomes,
   review backlog and latency.
3. Open **Fax Processing - Quality and Release** for type/source outcomes,
   rejection reasons, confidence bands, HL7 status and model/configuration usage.
4. Treat missing facts as unknown and confirm official counts against immutable
   jobs/manifests. The Workbooks use sampled telemetry.
5. Do not interpret confidence charts as accuracy. Use independent labeled audits
   and the [success criteria](success-criteria.md) for release decisions.
