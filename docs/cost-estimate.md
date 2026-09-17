# Azure fax-processing cost estimate

Estimate date: **2026-09-16**. Currency: **USD retail**, West US 2, 730
hours/month. Enterprise agreements, reservations, taxes, support, Power Platform
licenses, human review labor and downstream receiver costs are excluded.

## Executive estimate

| Scenario | Estimated cost |
|---|---:|
| Base production infrastructure, idle/low volume | **about $210/month** |
| Variable processing for 10,000 incoming pages, assuming 10% cover pages | **about $1,405** |
| Total month with base plus 10,000 incoming pages | **about $1,615/month** |
| Effective cost at exactly 10,000 pages/month | **about $0.162/incoming page** |
| Marginal cost after the base is already funded | **about $0.141/incoming page** |

Use a planning range of **$1,510-$1,715/month** for 10,000 pages before
contingency: approximately $1,300-$1,500 of Document Intelligence usage
(depending on the cover-page share), about $210 base, and a small allowance for
Storage, telemetry, Private Link data and scale-out.

## Base infrastructure

The reusable `infra/main.bicep` baseline creates one EP1 Functions plan, eight
private endpoints, two StorageV2 accounts, one S0 Document Intelligence account,
workspace-based Application Insights, Event Grid, alerts and Workbooks. It
expects the VNet/subnets, six private DNS zones, AMPLS and action group to exist.

| Component | Assumption and formula | Monthly estimate |
|---|---|---:|
| Azure Functions EP1 minimum instance | 1 vCPU x $0.16/hour + 3.5 GiB x $0.0114/GiB-hour = $0.1999/hour; x 730 | **$145.93** |
| Private endpoints | 8 x $0.01/hour x 730 | **$58.40** |
| Storage capacity and transactions | Low-volume host/data state, approximately 25 GB or less plus light operations | **$1-$5** |
| Document Intelligence S0 account | No fixed inference charge | **$0** |
| Event Grid | Low volume within free allowance | **$0** |
| Log Analytics/Application Insights | Up to the included first 5 GB/month; then $2.30/GB | **$0 initially** |
| Workbooks, managed identities, RBAC, alerts | No separate base resource charge | **$0** |
| Six private DNS zones, if not already shared | Approximately $0.50/zone/month | **+$3** |

Estimated incremental base: **$205-$209/month** when the required DNS/AMPLS/VNet
are already funded, or **$208-$212/month** if six dedicated DNS zones are charged
to this workload. Budget **$210/month** as the practical base.

The private synthetic test wrapper costs more because it also adds a VM, NAT
gateway/public IP, authoring Document Intelligence, training storage and three
additional private endpoints. Those resources are not included in this base
production estimate.

## Variable cost per 10,000 incoming pages

The runtime normally invokes Document Intelligence three times:

1. Page classifier on every incoming page.
2. Fax-type classifier on retained body pages.
3. Type-specific extractor on retained body pages.

The West US 2 S0 retail meter is **$50 per 1,000 custom pages**. Assuming 10% of
incoming pages are covers:

| Processing pass | Billable pages | Formula | Cost |
|---|---:|---:|---:|
| Page classification | 10,000 | 10 x $50 | **$500** |
| Fax-type classification | 9,000 | 9 x $50 | **$450** |
| Field extraction | 9,000 | 9 x $50 | **$450** |
| Document Intelligence subtotal | 28,000 page-operations | 28 x $50 | **$1,400** |

Allow approximately **$1-$5** more for Blob/Queue transactions and retained
copies, Private Link data processing, Event Grid operations, and safe telemetry
at this volume. If one EP1 instance handles the workload without scale-out, there
is no additional Functions compute beyond the base.

Estimated variable total: **about $1,405 per 10,000 incoming pages**.

### Sensitivity to cover-page percentage

Let `c` be the cover-page fraction. Estimated custom page-operations are:

`10,000 + 2 x 10,000 x (1 - c)`

| Cover share | Custom page-operations | DI cost | Approximate variable total |
|---:|---:|---:|---:|
| 0% | 30,000 | $1,500 | **$1,505** |
| 10% | 28,000 | $1,400 | **$1,405** |
| 20% | 26,000 | $1,300 | **$1,305** |

This estimate assumes all supported body pages complete both downstream model
passes. Retries, reprocessing, mixed-document handling, additional AI passes or
high-resolution/add-on features increase cost. Faxes rejected after page/type
classification may cost less because extraction is not invoked.

## Important exclusions and controls

- Human review labor is excluded and may exceed Azure variable cost.
- Power Apps/Power Automate licenses and enterprise network policy costs are
  excluded.
- Existing VNet, AMPLS, firewall/proxy, private DNS resolver and action group
  allocations are excluded.
- Storage retention/versioning grows with source size and retention policy.
- Log ingestion above 5 GB/month is estimated at $2.30/GB.
- An EP1 scale-out instance costs approximately another $0.1999 for each hour it
  remains active.
- Private Link data is approximately $0.01/GB in each metered direction.
- Validate the actual invoice after a controlled representative batch; retail
  meters do not include negotiated discounts.

## Pricing sources

- [Azure Retail Prices API - Functions, West US 2](https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Functions%27%20and%20armRegionName%20eq%20%27westus2%27)
- [Azure Retail Prices API - Form Recognizer/Document Intelligence, West US 2](https://prices.azure.com/api/retail/prices?$filter=contains(productName,%20%27Form%20Recognizer%27)%20and%20armRegionName%20eq%20%27westus2%27)
- [Azure Retail Prices API - Event Grid, West US 2](https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Event%20Grid%27%20and%20armRegionName%20eq%20%27westus2%27)
- [Azure Retail Prices API - Log Analytics, West US 2](https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Log%20Analytics%27%20and%20armRegionName%20eq%20%27westus2%27)
- [Azure Retail Prices API - Private Link](https://prices.azure.com/api/retail/prices?$filter=contains(meterName,%20%27Private%20Endpoint%27)%20and%20productName%20eq%20%27Virtual%20Network%20Private%20Link%27)

Prices can change. Re-run the estimate before procurement or release approval.
