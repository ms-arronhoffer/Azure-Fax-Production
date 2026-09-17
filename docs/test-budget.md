# Isolated test-environment spending controls

Initial deployment/test allowance approved by the user: **USD100; stop and ask
before exceeding it**. This is not authorization for indefinite operation.
No resources have been deployed merely by generating this document.

## Planning estimate

Retail estimates for the selected West US 2 configuration were retrieved on
2026-09-15. Contract discounts, taxes, credits and actual invoice rates differ.

| Meter | Planning rate | 72-hour assumption |
|---|---|---|
| Functions Premium CPU | USD0.16 / vCPU-hour | One EP1 vCPU |
| Functions Premium memory | USD0.0114 / GiB-hour | 3.5 GiB for one EP1 |
| Linux Standard_D2s_v7 runner | USD0.132 / hour | Runner allocated for 72 hours |
| Standard NAT gateway | USD0.045 / hour | One gateway |
| Standard private endpoints | USD0.01 / endpoint-hour | Eleven endpoints; verify actual template count |

For one EP1 instance, one allocated runner, NAT and eleven endpoints:

`hourly_base = 0.16 + 3.5 * 0.0114 + 0.132 + 0.045 + 11 * 0.01 = USD0.4869`

`72_hour_base = USD35.0568`

This **excludes** disks, Storage capacity/transactions, Azure Files, DNS,
outbound public IP, traffic, logs, and Document Intelligence training/inference.
At 730 hours, the same base alone is about USD355.44. Additional EP1 instances
increase cost; stopping the Function code does not stop plan billing.

Current **Azure Document Intelligence S0** retail meters subsequently resolved:

| Operation | Planning rate |
|---|---|
| Custom extraction pages | USD30 / 1,000 pages |
| Classifier pages | USD3 / 1,000 pages, per classification pass |
| Read pages | USD1.50 / 1,000 pages at the initial tier |
| Prebuilt/layout pages | USD10 / 1,000 pages |
| Training meter | USD3 / hour; check applicable model/free allowance rather than assuming free |

For a conservative initial reserve, 1,000 custom extraction pages + 2,000
classifier page-passes + 1,000 Read pages + five billed training hours total
USD52.50. Combined with the 72-hour base, that is USD87.56 **before other
resources/usage**, leaving little margin. The actual initial corpus/run should
be substantially smaller, and the counters must stop before this reserve is
spent. These page counts are ceilings for planning, not instructions to submit
that many pages or a claim that a build's billed hours are known in advance.

## Before and during a run

1. Require explicit provisioning approval after inspecting resource names,
   private-network exceptions, identity grants, what-if and current validation.
2. Record resource creation/start times, actual counts/SKUs and the deployment ID.
   Keep test resource groups distinct from unrelated workloads.
3. Bound generated training/test pages, model-build counts, DI request retries,
   polling duration, application scale and live-test reruns. Never automatically
   retrain until tests turn green.
4. Recalculate estimated accrued cost from elapsed resource time and metered
   operations before each training/test batch. Review actual Azure cost data when
   available, but remember billing data is delayed.
5. Keep a safety margin for delayed usage and teardown. If a batch's conservative
   estimated cost plus accrued spend can reach USD100, stop before the batch and
   request approval. Unknown/unbounded model pricing is a reason to verify or
   reduce the run, not to assume zero cost.
6. Report progress separately as local tests, real inference tests, intentional
   fault-injection tests, blocked tests and skipped tests. Failures do not justify
   unlimited paid retries.

## After testing or on interruption

- Deallocate the runner when not needed; its disks and network resources remain
  chargeable. Do not assume a shell exit, interrupted session or idle VM means
  deallocation.
- EP1's minimum allocation, NAT and private endpoints remain billable while the
  resources exist. A budget alert is not a hard cap or automatic shutdown.
- Present the retained resource list and ongoing estimate. Ask whether to retain
  or delete the **two dedicated test groups**; do not delete unrelated resources.
- Deletion requires confirmation and evidence export first. Deleting groups also
  deletes synthetic trained models, test results and storage unless separately
  preserved. Reusability comes from the Bicep, source, training scripts and
  versioned configuration, not keeping an unmonitored test environment alive.

## Sources and limitations

Meter data: [Azure Retail Prices API](https://prices.azure.com/api/retail/prices).
Premium minimum billing:
[Functions Premium plan](https://learn.microsoft.com/azure/azure-functions/functions-premium-plan#billing).

The pricing tool could not deserialize the AI service-family value. A bounded
three-page read of the public Retail API resolved current **Azure Document
Intelligence** S0 meters. Legacy **Form Recognizer** meters also appear and are
not substituted for the current product. Verify model-specific billing and any
free training allowance before a build; do not assume that an API return proves
the exact customer invoice or final build duration.
