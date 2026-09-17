# Document classification and document-specific extraction models

## What must be trained, and what is not supplied

This package does not contain a trained model or your organization's labeled fax
data. Model quality cannot be manufactured by infrastructure deployment. Keep
processing disabled until model IDs and expected-field definitions have passed
preflight and your held-out acceptance tests.

The isolated reference models were successfully built, promoted and invoked, but
their 2026-09-16 broad held-out exact-field result was only 12/18 faxes (66.7%).
Several identifiers/names had confident `O`/`0` or spacing substitutions. Treat
this as a concrete retraining example: do not promote these synthetic model IDs
as evidence of production accuracy, and do not infer correctness from confidence.

There are **two different model tasks**:

1. **Classification** identifies a cover page versus a supported body document
   type. Its output decides what pages to remove and which extractor to use.
2. **Extraction** identifies the business fields in the remaining body pages.
   Its field confidence scores decide whether the auto-read can be accepted.

A high classification score does **not** establish that every field was read
correctly. OCR word confidence is not a substitute for field confidence.
Read/Layout alone does not give you a business-specific expected-field schema.

Use the `2024-11-30` Document Intelligence API consistently for authoring,
validation and runtime. Custom model versions are separate immutable model IDs;
do not reuse an ID to mean different approved model versions.

## Network boundary: authoring is not the private production runtime

The production template disables public access to the runtime Document
Intelligence resource and data storage. The Function sends document **bytes**
over the private endpoint; Document Intelligence does not need to fetch a
production blob URL.

The approved authoring approach is a **separate, governed training environment**:

- Separate authoring Document Intelligence resource and training Storage account.
- Training data is de-identified where possible and explicitly approved for use.
- An administrator enables the Document Intelligence managed identity.
- Training storage uses selected networks with the documented **trusted Azure
  services** exception and the necessary identity permissions.
- Labelers work from an approved network-connected workstation.
- Trained models are promoted to the private runtime resource after evaluation.

**Important:** adding a Storage private endpoint does not give the managed
Document Intelligence training service a network interface inside your VNet.
The training service's server-side dataset fetch is different from your
workstation's private network access. Do not disable a runtime firewall or
publish production faxes to solve authoring connectivity.

Microsoft's secured-access guide describes the selected-networks/trusted-service
exception. Some Studio features, including auto-labeling, additionally use
Studio service connectivity. If an operation requires an authoring firewall
exception, obtain approval, scope it to the authoring environment, record it,
and remove it when no longer needed. Do not assume every Studio feature works
through a private endpoint simply because a browser can load Studio.

Before building models, confirm:

1. The workstation resolves the authoring resource and training Storage
   endpoints correctly.
2. A Read analysis submitted from that workstation succeeds.
3. The labeler can enumerate, open and save labels in the training container.
4. The Document Intelligence managed identity can read the training corpus.
5. A small build finishes successfully. Passing a browser labeling test alone
   does not prove the service can read the corpus.

See [RBAC](rbac.md) for the separate author, labeler, promotion and runtime roles.
The runtime Bicep intentionally does not provision this authoring exception.

## Step 1: define the business contract before collecting samples

For each document type, agree on:

| Decision | Example only |
|---|---|
| Stable document type key | `invoice` |
| Classifier body label | `invoice` |
| Cover label | `cover` |
| Extraction model ID | `invoice-neural-v1` |
| Expected fields | `InvoiceNumber`, `InvoiceDate`, `Total`, line-item fields |
| Validation contract | nonblank values; add domain checks for date ranges, account membership or cross-field totals before publication if required |
| Repeated records | every required cell in every line-item row |
| Source authorization | which caller is permitted to submit for which source |
| Conditional outputs | full accepted JSON copies selected by source, document type, content or required field values |

These are illustrative field names, not the supplied customer's actual schema.
Choose the real fields with the business owner and use identical spelling and
casing in labels, the model and the runtime configuration.

The shipped `expectedFields` configuration checks required leaf presence,
readability and confidence; it is not a configurable business-rule engine.
Extend the application and its tests before relying on domain-specific validation.

Define "expected" precisely:

- Mandatory fields must be configured and must not be silently dropped when
  absent in a document.
- For a field that is legitimately not applicable, define a separate document
  subtype/schema or an explicitly implemented applicability rule. Do not invent
  a value or confidence to satisfy the gate.
- Define table/array paths using the exact syntax supported by the runtime
  configuration. Refer to [the API and configuration contract](api.md).
- An empty schema is a configuration failure, not an automatic success.

## Step 2: collect and partition representative fax samples

Collect legal, approved examples of:

- Each sender, source system, layout revision and supported language.
- Low-resolution scans, skew, blur, noise, faint print and rotated pages.
- Documents with and without a cover, multiple covers, and cover-only faxes.
- Multipage documents and tables spanning page boundaries.
- Missing fields, zeros, false/unchecked values and contradictory totals.
- Mixed or unsupported body types, so their rejection behavior is tested.

Do not put real customer faxes into this repository or test fixtures. Store
training material in access-controlled storage, with an owner and retention
schedule.

Split into **training**, **validation** and **final held-out test** sets before
labeling. Keep duplicate scans and pages from the same fax in the same split.
Otherwise near-duplicate pages leak into testing and inflate reported quality.
Hold out entire senders/layout variants when testing generalization.

Service minimums are not quality targets:

- A custom classifier requires at least **two classes**, with at least **five
  samples per class**.
- Custom extraction can start with at least **five labeled documents**, but
  varied production faxes typically need substantially more.

Check current regional availability, dataset limits, supported field types,
training quota and billing before a large build.

## Step 3: train the cover/body classifier

1. Open Document Intelligence Studio from the approved workstation.
2. Select the authoring subscription and Document Intelligence resource.
3. Create a **custom classification** project, and select the training container.
4. Create labels matching the runtime configuration, for example `cover` and
   `invoice`. Add each supported body type separately.
5. Label representative cover pages and body pages. Include later body pages,
   not just the first page of a document.
6. Build a versioned classifier, for example `fax-pages-v1`.
7. Analyze a held-out multipage fax using **`splitMode=perPage`**.
8. Inspect the result: every page must have the intended class, page number and
   usable confidence. Verify that a low-confidence page cannot cause an
   irreversible page deletion in your workflow.

The classifier assigns known labels; it is not an infallible unknown-document
detector. Unseen inputs can still receive a confident known label. Test these
inputs and establish a separate classification threshold and review policy.

`perPage` is appropriate for identifying covers one page at a time.
`auto` identifies document boundaries for a workflow explicitly designed to
process bundled documents. In v4 the default is **`none`**, so do not omit this
setting and assume splitting happens.

The REST query parameter for the v4 classify request is **`split=perPage`**;
`splitMode` is terminology used in the conceptual documentation/SDK surfaces.
Do not send an invented `splitMode` query parameter to the REST endpoint.

Classifier page ranges are metadata. The Function must actually create a new
PDF without the identified cover pages. Keep the original and original-to-body
page map for audit and reviewer interpretation.

## Step 4: choose an extractor

### A. Use a prebuilt model when its schema fits

For a standard invoice, evaluate `prebuilt-invoice` before creating a custom
model. Other prebuilt models cover other common business documents. Verify
that the exact required fields and their confidence scores are present on your
fax samples. A prebuilt model that reads most fields is not sufficient if a
mandatory field is unavailable.

### B. Custom template extraction

Use custom template extraction for consistent, fixed-layout forms. Train and
evaluate layout variants separately when positioning changes significantly.

### C. Custom neural extraction

Use custom neural extraction for structured or semi-structured forms with
layout variation. It is typically the first custom option to evaluate for faxes
from multiple senders. Neural does not mean arbitrary unseen forms will work.

## Step 5: label and train a custom extractor

1. Create a **custom extraction** project in Document Intelligence Studio.
2. Select the training container/folder for one document family.
3. Open each training document and run OCR as required by Studio.
4. Define each business field with the correct type: string, date, number,
   currency, selection mark, signature or supported structured/table type.
5. Label the actual value regions, not the printed field captions.
6. Label repeated rows/cells consistently. For a table spanning pages, keep the
   rows within the same logical table definition.
7. Include positive and negative examples of optional features such as
   signatures. Do not label missing values as if they existed.
8. Inspect every annotation for wrong regions, inconsistent names and types.
9. Build using **neural** or **template**, with an immutable versioned model ID.
10. Review build warnings/errors and field-level metrics.
11. Analyze the held-out **body-only** documents using that exact model ID.
12. Inspect the JSON, including `documents[].fields` and each required field's
    `confidence`. Test table cells individually where the schema requires them.

The extraction model must see the same preprocessing in testing and production:
cover removal, TIFF normalization and original/body page mapping. Training on
pristine digital PDFs alone is usually not representative of actual faxes.

### REST equivalents for build automation

These are request shapes for an authenticated authoring client, not commands
that upload any data from this package. Use an Entra token for
`https://cognitiveservices.azure.com/.default`; never put tokens in source,
shell transcripts or pipeline logs.

Build an extractor:

```http
POST https://AUTHORING.cognitiveservices.azure.com/documentintelligence/documentModels:build?api-version=2024-11-30
Authorization: Bearer <token>
Content-Type: application/json

{
  "modelId": "invoice-neural-v1",
  "description": "Approved invoice fax body schema v1",
  "buildMode": "neural",
  "azureBlobSource": {
    "containerUrl": "https://TRAINING.blob.core.windows.net/training",
    "prefix": "invoice-v1/"
  }
}
```

This folder must include the label/schema artifacts produced by the supported
labeling tool. Placing PDFs in a folder without labeling them does not create a
business-specific extraction model.

Build a classifier:

```http
POST https://AUTHORING.cognitiveservices.azure.com/documentintelligence/documentClassifiers:build?api-version=2024-11-30
Authorization: Bearer <token>
Content-Type: application/json

{
  "classifierId": "fax-pages-v1",
  "description": "Cover and invoice page classifier",
  "docTypes": {
    "cover": {
      "azureBlobSource": {
        "containerUrl": "https://TRAINING.blob.core.windows.net/classification",
        "prefix": "cover/"
      }
    },
    "invoice": {
      "azureBlobSource": {
        "containerUrl": "https://TRAINING.blob.core.windows.net/classification",
        "prefix": "invoice/"
      }
    }
  }
}
```

Use the authoring resource's managed identity for these dataset reads; authorize
it and the storage network path as described above. Poll the returned
`Operation-Location` until `succeeded` or `failed`, honoring throttling and
timeouts. Validate that returned URLs belong to the expected service endpoint
before attaching a token. A `202` response only means the operation was accepted.

## Step 6: evaluate the exact production acceptance policy

The user-selected policy is:

> All expected fields must be present, valid, and have confidence >= 0.80.
> Missing confidence, an unreadable value, or a score below 0.80 rejects the
> entire auto-read. Exactly 0.80 passes.

Record the minimum expected-field score as a summary, **not** an average. A
document with field scores `0.99, 0.99, 0.79` must be rejected. Neither a high
average nor a high document-type score overrides the weak field.

Confidence is an estimate, not a guarantee that 80% of documents or fields are
correct. Choose held-out business acceptance targets separately:

- Exact-match/normalized accuracy per field, including numeric/date handling.
- Incorrect auto-accept rate (the most important safety metric).
- Rejection/human-review rate by sender and document family.
- Cover removal precision, including body pages incorrectly removed.
- Accepted documents with **all** mandatory fields correct.
- End-to-end latency, throttling rate and review backlog age.

Do not lower the 0.80 threshold to make an acceptance test pass. Improve training
data, field labeling, preprocessing or the schema, then evaluate again.

## Step 7: promote models without opening production data storage

Use the Document Intelligence model-copy APIs from an approved runner able to
reach both authoring and target service endpoints:

1. On the **target** resource, call
   `POST /documentintelligence/documentModels:authorizeCopy?api-version=2024-11-30`
   with the new target `modelId` and description.
2. Treat the returned authorization payload as a **secret**. Keep it in memory;
   it includes a short-lived access token. Do not check it into Git or print it.
3. On the **source**, call
   `POST /documentintelligence/documentModels/{sourceModelId}:copyTo?api-version=2024-11-30`
   with the target authorization payload.
4. Poll the operation to completion; then GET the target model and analyze a
   synthetic/sanitized known test document against it.
5. Promote the classifier using the v4 classifier copy operations
   `documentClassifiers:authorizeCopy` and
   `documentClassifiers/{classifierId}:copyTo`. Verify compatibility in the
   target region and API version; do not assume older classifier versions can
   be copied.
6. If network policy, region or encryption settings block copy, stop promotion.
   Resolve the supported service configuration with the platform team. Do not
   temporarily enable public production inference as an automatic workaround.

Copy is a service operation, not an export of model weights into this package.
Test it with the target's **actual disabled-public-access settings** before the
first release. A documented API is not evidence that a specific tenant's
network/security policy permits it.

## Step 8: configure and release

1. Record the approved runtime classifier ID, extraction model IDs, label
   mappings, expected-field schema, configuration version and evaluation report.
2. Replace the example configuration using the exact structure in
   [API/configuration documentation](api.md).
3. Run local configuration validation and the live model preflight on the
   private deployment runner.
4. Deploy configuration and code together, with processing disabled.
5. Execute the end-to-end acceptance matrix in [operations](operations.md).
6. Enable admission only after approval. Monitor the first controlled batch.
7. Retain the previous model IDs and package so a code/config rollback is
   possible. Keep already admitted jobs tied to their configuration snapshot.

## Continuous improvement

### Correct confident OCR substitutions before another release attempt

The observed `O`/`0` misses must be handled as model/data-quality defects, not by
raising a confidence score or globally replacing characters:

1. Add adjudicated examples for each affected identifier pattern and source,
   including legitimate letters and digits so the model learns the distinction.
2. Include low-resolution, skewed, compressed and light/dark scans; PDF and TIFF;
   cover/no-cover; each source; and every supported document type.
3. Keep patient/source episodes together in one partition to prevent leakage.
4. Add authoritative validation only when the business contract defines the
   identifier grammar or an approved reference lookup. Ambiguous values route to
   review; validation must not silently mutate extracted clinical data.
5. Preserve numeric-run whitespace normalization only for its explicitly
   configured identifier fields. It is not an `O`/`0` correction.
6. Build versioned candidates, run development checks, then evaluate an untouched
   held-out cohort against the exact runtime gate and HL7 mappings.
7. Promote only after every mandatory success-criteria row passes. Retain the
   previous model IDs/configuration, deploy disabled, smoke-test a bounded cohort,
   and then enable admission.
8. On a failed bounded release, disable admission and restore the previous
   configuration/model IDs. Reconcile using the immutable manifests.

### Add a new fax type and extraction schema

1. Obtain clinical/data-owner approval for the type definition, supported sources,
   required fields, repeating groups, data types and nullability.
2. Define the canonical schema and explicit HL7 profile or approved
   `NotApplicable` policy. Never use an empty required-field schema.
3. Add independently labeled positives plus confusing neighboring and
   unknown/mixed negatives to the fax-type classifier.
4. Train a type-specific extractor, or select a prebuilt model only when its
   returned schema exactly fits.
5. Add the label, model ID/docType, ordered field contract, routes and HL7 mapping
   to a new versioned configuration. Do not overwrite a qualified version.
6. Add tests for PDF/TIFF, cover/no-cover, missing/low-confidence fields, invalid
   values, review correction, routing, HL7 success and fail-closed transformation.
7. Run the per-type and overall [success criteria](success-criteria.md). Keep the
   type review-only until its own strata pass; aggregate performance cannot qualify
   a weak type.
8. Promote and deploy with model, configuration, schema and mapping versions
   recorded in every completion manifest.

Review rejected samples with qualified reviewers. Corrected data is not
automatically safe training data: obtain consent/approval, remove prohibited
content, prevent duplicates, then add it to a new training version. Do not
silently train on unverified model outputs.

Monitor for new fax layouts, source changes, confidence drift and a rising
incorrect-auto-accept rate. Re-run the same held-out suite after every model,
schema, dependency or preprocessing change.

## Microsoft references

- [Custom classification and split modes](https://learn.microsoft.com/azure/ai-services/document-intelligence/train/custom-classifier?view=doc-intel-4.0.0)
- [Custom neural extraction](https://learn.microsoft.com/azure/ai-services/document-intelligence/train/custom-neural?view=doc-intel-4.0.0)
- [Custom template extraction](https://learn.microsoft.com/azure/ai-services/document-intelligence/train/custom-template?view=doc-intel-4.0.0)
- [Confidence and accuracy](https://learn.microsoft.com/azure/ai-services/document-intelligence/concept/accuracy-confidence?view=doc-intel-4.0.0)
- [Managed identities and secured access](https://learn.microsoft.com/azure/ai-services/document-intelligence/authentication/managed-identities-secured-access?view=doc-intel-4.0.0)
- [Model copy / disaster recovery](https://learn.microsoft.com/azure/ai-services/document-intelligence/disaster-recovery?view=doc-intel-4.0.0)
- [v4 classifier copy authorization](https://learn.microsoft.com/rest/api/aiservices/document-classifiers/authorize-classifier-copy?view=rest-aiservices-v4.0%20(2024-11-30))
