# Identity, application roles and Azure RBAC

## Three independent access checks

A private endpoint is **not** an authorization policy. A successful request
requires all of these:

1. **Network:** DNS/routing/firewall allows the client to reach the private
   resource.
2. **Token:** the resource accepts the tenant, issuer, audience and token.
3. **Authorization:** the caller has the specific application role or Azure
   data-plane role required for the action.

Entra application roles such as `Fax.Submit` are **not Azure RBAC roles**.
Conversely, subscription Contributor does not grant permission to call a
protected business API or read blob contents with Entra authentication.

## Personas and boundaries

| Principal | Needed permissions | Scope / purpose | Not granted |
|---|---|---|---|
| Infrastructure deployer | Contributor plus Role Based Access Control Administrator, or constrained equivalents including role-definition and role-assignment writes | Workload resource group | Permanent Owner across the subscription |
| Network deployment operator | Network Contributor or specific subnet/private-endpoint join/write actions | Existing integration/endpoint subnets and relevant network resources | Rights on unrelated VNets |
| Private DNS operator | Private DNS Zone Contributor | Existing zones used by workload endpoint groups and network links | Broad subscription DNS control |
| Private monitoring operator | Appropriate monitoring resource writes and private-link-scoped resource permissions | Existing Azure Monitor Private Link Scope (AMPLS) | Public monitoring enablement as a workaround |
| Function runtime system identity | See exact runtime matrix below | Runtime resources only | Model build/delete/copy administration; Entra directory administration |
| Power Automate connector user/service identity | `Fax.Submit` app role plus approved delegated scope/client as applicable | Fax API enterprise application | Direct Storage/Document Intelligence access |
| API reviewer | `Fax.Review` application role | Cross-owner status/facts, review evidence/original download and reviewed release through private API | Direct arbitrary accepted-output writes; submission without `Fax.Submit` |
| Cross-owner status administrator | `Fax.Admin` app role | Read other owners' job status; assign sparingly | Submission, recovery/reprocessing/release API, Azure administration |
| Review investigator using Storage tools | Storage Blob Data Reader | `human-review` container; add original container read only if needed | Subscription Contributor, container deletion |
| Output consumer | Storage Blob Data Reader | `extracted`, `routed`, and required job/manifest read scope | Writes to outputs or review decisions |
| Model labeler | Storage Blob Data Contributor and appropriate DI authoring role | Authoring training containers and authoring DI resource | Runtime production faxes |
| Model builder | Cognitive Services Contributor; training data access as needed | Authoring DI resource, not entire subscription | Production runtime administration |
| Authoring DI managed identity | Documented Storage data role for dataset fetch | Training container/account according to supported authoring configuration | Production Storage |
| Model promotion identity | Cognitive Services Contributor or tested custom role with copy/authorize-copy/read operations | Source and destination DI resources | Runtime data-container access |
| Code deployment identity | Website Contributor or custom role allowing site deployment/configuration reads needed by release tool | Function App; ARM access plus private SCM network access | Unrestricted role assignment |
| Monitoring/on-call | Monitoring Reader / Log Analytics Reader | Workload monitoring resources | Business document contents by default |

These are recommended functional boundaries. Azure built-in roles may include
more operations than an organization permits. Use PIM/JIT elevation for
deployment, authoring and promotion; test scoped custom roles before replacing
built-ins.

## Function runtime: exact permission purpose

The Bicep implementation and [infrastructure guide](infrastructure.md) are the
source of truth for the actual role-definition IDs and assignment scopes.
Review them during `what-if`; verify there are no unexpected account/subscription
assignments.

| Resource | Required operations / typical role | Why |
|---|---|---|
| Business Blob containers | Storage Blob Data Contributor, container-scoped where possible | Intake originals; working data; status/manifest; accepted/routed/review artifacts |
| `fax-jobs` queue | Storage Queue Data Contributor or equivalent scoped sender+processor permissions | Send work and consume/delete messages |
| Poison queue | Queue data permissions appropriate to handler/host | Runtime retry exhaustion and operator recovery |
| Host Blob service | Storage Blob Data Owner per Functions identity-based host-storage guidance | Host leases, secrets/checkpoints and Durable blob artifacts |
| Host Queue service | Storage Queue Data Contributor | Durable work/control messages |
| Host Table service | Storage Table Data Contributor | Durable instance/history tables |
| Runtime Document Intelligence | Cognitive Services User, DI-account scope | Tested classify/analyze/result access; dedicated runtime account limits the broader data-plane role |
| Application Insights (when Entra ingestion is enabled) | Monitoring Metrics Publisher | Authorized telemetry ingestion |

Container-level grants for business data reduce access outside the workload.
The processing identity nevertheless necessarily handles all artifacts of this
pipeline. For stronger segregation, split intake, processing and review into
separate apps/identities; this package does not claim that one identity is a
cross-tenant isolation boundary.

The original template had 13 runtime assignments. The direct-Blob revision adds
ready-manifest access and Blob-trigger host permissions, and the standalone
deployment adds separate uploader, reviewer, author and deployment identities.
Use the current [infrastructure guide](infrastructure.md) and compiled template
as the authoritative assignment inventory rather than relying on the old count.

### Document Intelligence runtime role

`Cognitive Services User` (`a97b65f3-24c7-4388-baec-2e87135dc908`) is assigned
only on the dedicated runtime Document Intelligence account. Live authorization
tests returned HTTP 401 with the narrower six-DataAction custom role and succeeded
with this built-in role, so the reusable Bicep matches the tested deployment.

The rejected custom-role experiment contained these six DataActions:

```text
documentmodels:analyze/action
documentclassifiers:analyze/action
documentmodels/analyzeresults/read
documentclassifiers/analyzeresults/read
documentmodels/read
documentclassifiers/read
```

The runtime role assignment remains scoped to the runtime DI resource, not the
resource group. The deployment output
`documentIntelligenceRuntimeRoleDefinitionId` identifies the built-in definition.

Permissions are additive: if an environment already grants the runtime a broad
role, this custom role does not revoke it. Review inherited and existing direct
assignments before asserting that model builds/copies/deletes are denied.

### Host storage is not the same as business storage

Identity-based `AzureWebJobsStorage` authenticates host/Durable operations; it
does **not** automatically make a Premium Azure Files content mount keyless.
Review the infrastructure implementation's Azure Files/packaging choice and
any required platform connection setting separately.

If a content-share connection uses an account key, it must be restricted to
the **separate host account**, stored only in protected platform configuration,
never printed/output, and rotated using the documented release procedure.
Do not disable shared keys on that host account without verifying the content
mount/deployment design. Business Storage should remain Entra-only.

Never output `listKeys()` values, app-setting dumps, or private connection
strings into pipeline logs. Limiting human access to Function configuration is
also important because a user who can alter code/config can act as the runtime
identity.

## Entra application registration: business API

This is a tenant-level prerequisite, not automatically created by the ARM
Bicep deployment.

1. In Entra ID, create a **single-tenant** app registration for the Fax API.
2. Record its **Application (client) ID**. This becomes the Bicep API client ID
   parameter and the token audience configuration.
3. Set the Application ID URI, for example `api://<api-client-id>`.
   Set the registration's `api.requestedAccessTokenVersion` to **2**. The runtime
   expects a v2 access token whose `aud` is the API **client-ID GUID**, configured
   by Bicep as `ENTRA_API_AUDIENCE`. The scope still uses the Application ID URI.
4. Expose a delegated scope such as `access_as_user`. Restrict consent to
   administrators according to organizational policy.
5. Define app roles:

   | Display name | Value | Allowed members |
   |---|---|---|
   | Submit faxes | `Fax.Submit` | Users/Groups; Applications only if app-only submission is intended |
   | Review faxes | `Fax.Review` | Approved reviewers; synthetic test reviewer application only in the isolated test environment |
   | Read all fax statuses | `Fax.Admin` | Designated status administrators only |

6. In the API **enterprise application**, enable assignment required where
   appropriate. Assign approved submitters to `Fax.Submit`; assign `Fax.Admin`
   separately only when cross-owner status access is required.
7. For an app-only integration, grant the **application permission/app role**
   to its service principal and obtain administrator consent. App-only uses
   `.default`, not a delegated user scope.
8. Configure EasyAuth with the exact tenant issuer and accepted audience(s).
   Require authenticated requests with HTTP 401, not a browser login redirect.
9. Restrict allowed client applications to the approved connector/client IDs.
10. Verify that access tokens actually contain the assigned `roles` values.
    An access token with only a delegated `scp` is insufficient if the app
    requires an app role.

Do not confuse:

- **Application/client ID:** identifies the app; used for OAuth client/audience.
- **Application object ID:** directory object for the registration.
- **Service principal object ID:** enterprise application instance; used for
  Azure RBAC assignments to an application.
- **User object ID:** identifies an individual; useful for ownership/audit.

Use object IDs, not display names, for assignments and application configuration
that binds identity to an allowed source. Names can collide or change.

## Power Automate OAuth client

Create a separate single-tenant client registration for the custom connector.
Configure delegated permission to the Fax API scope and admin consent, then
register the exact redirect URI shown by the connector.

Store any connector OAuth client secret only in the connector's secured
connection configuration. Prefer the organization's approved credential
management/rotation mechanism. It must not appear in the Bicep parameters,
Python source, sample configuration or deployment logs.

The token's user is the flow connection user when a flow uses an owner's
connection. It is **not necessarily the Power Apps person who clicked Upload**.
Record that distinction in your audit design. If per-user authorization is
required, configure per-user connections and test the resulting token identity.
Do not trust an arbitrary `submittedBy` field as proof of caller identity.

## Least-privilege review access

Review the actual endpoints in [api.md](api.md). An application role does not
create an approval UI by itself.

The revised API implements review evidence, original download and a versioned
review POST. It requires `Fax.Review`, an `If-Match` review version, confirmed
type/body pages, complete corrected values and an audit reason. The runtime
validates and transforms reviewed data before committing a revision's manifest;
reviewers cannot bypass the required HL7 checks or alter machine confidence.

The automated test reviewer managed identity verifies the **authorization and
workflow mechanics** using synthetic expected values. It is not an actual human
assessment and must not be counted as evidence of human clinical accuracy.
Actual use requires named delegated reviewers and an approved review process.

For an external reviewer accessing rejected artifacts through Storage tools,
assign Storage Blob Data Reader only on the `human-review` container. Corrections
should be made through a separately authorized review/reprocessing workflow,
not by editing accepted-output blobs with a broad contributor role.

Review content can contain sensitive extracted data and original fax bytes.
Do not make the container public, distribute long-lived SAS URLs, or add
all employees to a reviewer group.

## Direct Blob upload and isolated runner identities

Original bytes are uploaded before the ready manifest. Access to `incoming` and
`ready` is restricted to the trusted integration uploader; source metadata is
checked against configured source labels. The manifest's source or configured
integration ID is **not** proof of the person who sent the fax. The runtime
records this distinction and preserves a restricted admitted copy of the input.
Use the authenticated HTTP route if individual caller identity is required.

The private test runner can hold distinct managed identities for uploading,
reviewing, model authoring/qualification and deployment. Their tokens and roles
are tested separately. Anyone able to run arbitrary commands on that VM can
potentially obtain the attached identities' tokens; therefore this is a trusted
isolated test boundary, not separation from a malicious VM administrator.
Do not reuse this multi-role runner as an untrusted production client.

- Uploader: only input/ready data access plus the submission app role.
- Reviewer: review app role, no direct accepted-output writes.
- Author/qualification: authoring model/training access, controlled promotion to
  runtime DI and read-only test-output verification as configured.
- Deployer: private application deployment rights; no implicit review approval.
- Runtime: required private trigger/host/data operations plus the custom DI
  inference/metadata role, not model-authoring permissions.

Directory application creation and application-role assignment are separate from
Azure subscription RBAC. If those permissions are denied, request an approved
registration/assignment from the tenant administrator; do not replace EasyAuth
with anonymous endpoints or fabricate principal headers.

## Verify and audit assignments

Before deployment, resolve built-in role IDs by **role name** in the target
cloud/tenant rather than copying unrelated GUIDs:

```powershell
az role definition list --name "Storage Blob Data Contributor" `
  --query "[].{Name:roleName,Id:name}" --output table
```

After deployment, use the portal Access Control / Check access view or scoped
role-assignment listing for each runtime resource. Allow for Entra/RBAC
propagation, then verify with real token-based data-plane calls.

Test both allowed and denied operations:

- Submitter can upload and see only authorized jobs.
- Submitter cannot review or read another owner's job.
- Reviewer can inspect rejected records using the supported workflow.
- Runtime can read/write required artifacts but not author models.
- Consumer can read completed artifacts, not mutate them.
- Outside-network callers cannot bypass private access even with valid roles.

## References

- [Azure built-in roles](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles)
- [Functions identity-based connections](https://learn.microsoft.com/azure/azure-functions/functions-reference#configure-an-identity-based-connection)
- [Durable Functions identity-based connections](https://learn.microsoft.com/azure/azure-functions/durable/durable-functions-configure-managed-identity)
- [App Service authentication](https://learn.microsoft.com/azure/app-service/overview-authentication-authorization)
- [Entra application roles](https://learn.microsoft.com/entra/identity-platform/howto-add-app-roles-in-apps)
