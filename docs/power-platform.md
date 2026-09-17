# Power Apps and Power Automate integration

## Scope

The Azure Bicep deploys Azure resources, not a tenant-specific Power Apps canvas
app, Power Automate flow, Entra registrations, connector connections or a Power
Platform enterprise network policy. These steps create those tenant-bound
components using the supplied API. They must be saved in your organization's
Power Platform solution and promoted through its ALM process.

The approved design assumes Power Platform private connectivity already exists.
Validate that assumption with the **actual connector and environment**; do not
substitute an ordinary public HTTP action and assume it can reach a private
endpoint.

"Private runtime" refers to the Azure application/data-plane endpoints and the
connector-to-Azure path. It does not turn the Power Apps/Power Automate SaaS
portals or Entra sign-in into privately hosted services.

## 1. Confirm private network integration

1. Ask the Power Platform administrator to identify the environment and linked
   enterprise network policy.
2. Verify that this environment and the intended custom connector support VNet
   connectivity in your region and licensing configuration.
3. Confirm that the Power Platform network can route to the Function App private
   endpoint subnet. The Power Platform delegated subnet is separate from the
   Function integration subnet and private endpoint subnet.
4. Confirm private DNS resolution for `<function-app>.azurewebsites.net`.
   Its CNAME chain must resolve through `privatelink.azurewebsites.net` to the
   private IP from this client's network.
5. Use a harmless authenticated API call from the connector to prove the route.
6. Keep the application API and SCM private/default-deny. The deployed Function
   host permits public ingress only from the `AzureEventGrid` service tag for the
   Event Grid webhook; that exception is not a public client API path.

NSG/UDR/firewall rules must allow the appropriate private HTTPS path. Private
DNS zone links alone do not configure a custom DNS server; set up conditional
forwarding/Azure DNS Private Resolver as needed.

## 2. Configure authentication

Follow [RBAC and Entra setup](rbac.md):

- Single-tenant Fax API registration with the exposed delegated scope.
- Assigned `Fax.Submit` app role for the flow connection identity.
- Separate approved OAuth client registration for the custom connector.
- Exact audience/tenant/client allowlist matching EasyAuth.
- Connector callback URI registered on the OAuth client.

Do not use a Function key as a replacement for Entra authorization. The HTTP
binding may be anonymous at the Functions host level because EasyAuth and the
application role check provide the security boundary.

## 3. Create the custom connector

1. In a dedicated Power Platform solution, create a custom connector. Start with
   [fax-api.openapi.json](../power-platform/fax-api.openapi.json), replacing its
   host, tenant and API client/scope placeholders before import.
2. Set its host to `<function-app>.azurewebsites.net`, base path `/api`, and HTTPS.
3. Configure Microsoft Entra ID OAuth 2.0 using the approved tenant, client ID,
   secret/credential and API resource/scope settings for your connector editor.
4. Save once, obtain its generated redirect URL, and add that exact URL to the
   client registration. Recreate the connection after consent changes.
5. Define the operations from [api.md](api.md). Do not invent routes or bypass
   the admission endpoint with a direct raw-blob upload.
6. Define the upload body as **binary file content**, with the required headers
   from the API contract; it is not a JSON string containing base64.
7. Define the status operation returning JSON. If building a separate reviewer
   app, define the governed GET/POST review and original-download operations from
   the API contract and require the `Fax.Review` role.
8. Create a connection as an assigned user and perform positive and negative
   authorization tests.

Respect the API's byte/page limits and the connector/platform limits; use the
smallest applicable limit. Test multipart/base64 conversions carefully: the API
must receive real PDF/TIFF bytes, not the base64 characters or a multipart
envelope.

## 4. Build the Power Automate upload flow

1. Create an instant cloud flow using **Power Apps (V2)**.
2. Add a File input and source input. Add optional business metadata only where
   supported by the API contract.
3. Generate an idempotency UUID **once**, before the upload action. Reuse it on
   retries of the same submission. Do not generate a new UUID inside a retry.
4. Extract the file's content bytes. When the trigger exposes `contentBytes`,
   use the platform's binary conversion where needed; verify with a PDF header
   test in a non-sensitive environment.
5. Call the custom connector upload operation with:
   - PDF or TIFF content type.
   - The actual file bytes.
   - The API source header using an authorized source value.
   - The same idempotency key for retries.
6. Configure bounded retry for transient network/429/5xx failures. Do not retry
   an authorization failure or idempotency conflict as if it were transient.
7. On HTTP 202, return the fax ID and receipt/status information to Power Apps.
8. In a Catch/error scope, return a clear failure message without including
   file contents, tokens, internal stack traces or service keys.

**202 means accepted for asynchronous processing, not successful extraction.**
If the client times out, retry with the original idempotency key. A second UUID
creates a new logical submission rather than recovering the same receipt.

Enable **Secure Inputs** and **Secure Outputs** on actions carrying the file,
token-bearing data, extraction content or reviewer information. Restrict flow
run-history access and retention. Do not log raw fax content in Compose actions.

## 5. Build the Power Apps upload screen

1. Use an approved file-upload/attachment control appropriate for the app type.
   Canvas attachment controls have form/data-source constraints; use a supported
   attachment form pattern rather than assuming a control is standalone.
2. Limit users to one submitted fax per API call and display supported types.
3. Collect the source from an authorized choice list, not a freeform destination
   path.
4. Call the upload flow and display the returned fax ID.
5. Display "Uploaded / Processing" rather than "Read successfully".
6. Poll status using a timer or separate status flow at a bounded interval;
   stop polling on terminal status or when the user leaves the screen.
7. Display accepted, pending human review and operational failure distinctly.
8. Prevent accidental double-click submissions and retain the submission's
   idempotency key until the result is known.

Keep business content out of notifications and client-side diagnostics. For
failed submissions, show an actionable message and correlation ID.

## 6. Reviewer workflow

The supplied API implements ETag-protected review and reviewed release, but the
tenant-specific Power Apps reviewer screen is not generated by Bicep. Build it in
the governed solution and restrict it to the approved reviewer group. Follow the
[How to use review procedure](how-to-use.md#review-a-rejected-or-uncertain-fax)
and [API contract](api.md#authorized-human-review).

1. Fetch only records the reviewer is authorized to access.
2. Show the original, body/original page mapping, required fields, each
   confidence score and the rejection reasons.
3. Highlight scores below 80% and missing fields; do not hide the original
   confidence after correction.
4. Require an explicit decision and rationale.
5. Use the API's concurrency token/ETag where required.
6. Preserve the actor/time/correction audit.

Do not create a flow that blindly copies review JSON to accepted output and
calls that an approval. Such a copy bypasses the explicit quality gate and
does not create a valid completion manifest or review audit.

## 7. Solution promotion

- Put the app, flows, custom connector, connection references and environment
  variables in a solution.
- Use separate development/test/production connections and API URLs.
- Apply a DLP policy preventing fax content from crossing into unapproved
  consumer connectors.
- Export/import the solution using the organization's Power Platform pipeline;
  rebind connection references and approve consent in the target environment.
- Run a private-connectivity/authentication smoke test after import.
- Record the Azure package version, model/config version and Power Platform
  solution version together.

## References

- [Power Platform VNet support](https://learn.microsoft.com/power-platform/admin/vnet-support-overview)
- [Custom connectors](https://learn.microsoft.com/connectors/custom-connectors/)
- [Power Apps attachment control](https://learn.microsoft.com/power-apps/maker/canvas-apps/controls/control-attachments)
- [Power Platform solutions](https://learn.microsoft.com/power-apps/maker/data-platform/solutions-overview)
