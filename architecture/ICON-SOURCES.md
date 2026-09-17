# Azure icon attribution and sources

## Source and authorized use

The Azure service artwork is **Microsoft's official Azure architecture icon collection**, not locally drawn approximations.

- Publisher / artwork owner: **Microsoft Corporation**
- Usage guidance and icon terms: <https://learn.microsoft.com/en-us/azure/architecture/icons/>
- Download used: <https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip>
- Retrieved: **2026-09-08**
- Use in this package: architecture diagrams and directly related documentation.

Microsoft's published terms permit these icons in architectural diagrams, training materials, and documentation. The source guidance prohibits cropping, flipping, rotating, distorting, or changing icon shapes, and prohibits using product icons as one's own product/service identity. Microsoft reserves other rights; use does not imply endorsement.

The SVG files here are unmodified extracted archive bytes. The diagrams preserve aspect ratio and original icon colors; colored node borders are separate diagram shapes and do not alter the artwork. Service names appear beside the icons.

## Exact selected artwork

Each path below is relative to `Azure_Public_Service_Icons/Icons/` in the archive.

| Local key | Archive SVG entry | Meaning in the diagram |
| --- | --- | --- |
| `functions` | `compute/10029-icon-service-Function-Apps.svg` | Azure Functions intake, Durable/worker, processing activities |
| `storage` | `storage/10086-icon-service-Storage-Accounts.svg` | Separate Function host storage account |
| `blob` | `general/10839-icon-service-Storage-Container.svg` | Private business Blob containers and artifacts |
| `queue` | `general/10840-icon-service-Storage-Queue.svg` | Azure Storage Queue job delivery |
| `documentIntelligence` | `ai + machine learning/00819-icon-service-Form-Recognizers.svg` | Azure AI Document Intelligence; archive uses its former Form Recognizer name |
| `privateEndpoint` | `other/02579-icon-service-Private-Endpoints.svg` | Function App private endpoint |
| `virtualNetwork` | `networking/10061-icon-service-Virtual-Networks.svg` | Existing VNet connectivity / subnet contract |
| `dns` | `networking/10064-icon-service-DNS-Zones.svg` | Existing Azure private DNS zone configuration |
| `identity` | `identity/10227-icon-service-Managed-Identities.svg` | System-assigned managed identity |
| `insights` | `monitor/00012-icon-service-Application-Insights.svg` | Application Insights |
| `logs` | `monitor/00009-icon-service-Log-Analytics-Workspaces.svg` | Log Analytics workspace |
| `monitor` | `monitor/00001-icon-service-Monitor.svg` | Azure Monitor / AMPLS capability |

`icons\manifest.json` records the source URL, archive SHA-256, exact archive entries, local filenames, and SHA-256 of every extracted SVG. The generator verifies each icon against that manifest before embedding it.

Power Apps, Power Automate, logical decisions, and the generic private build runner use explicitly labeled non-Azure boxes. They do not masquerade as Azure service icons. No third-party icon set or remotely hosted diagram image is used.

## Embedding and reproduction

- SVG previews embed bytes as standard `data:image/svg+xml;base64,...` image sources.
- Editable draw.io cells use mxGraph's SVG style convention `image=data:image/svg+xml,<base64>;`. The style omits the `;base64` token because semicolons delimit mxGraph styles; the embedded payload is still the exact SVG bytes encoded as base64.
- `imageAspect=1` / `aspect=fixed` in draw.io and `preserveAspectRatio="xMidYMid meet"` in SVG preserve the icon shape.
- The standalone previews and draw.io file do not need the adjacent icon files at viewing time.
- `acquire-icons.ps1` preserves original bytes and creates the provenance manifest. `generate-diagrams.cjs` renders native diagram elements and embeds those bytes without modifying their SVG contents.

Microsoft can update an archive at the same URL. The recorded hash identifies the downloaded revision, while the checked-in selected SVGs make this package reproducible without a new download.
