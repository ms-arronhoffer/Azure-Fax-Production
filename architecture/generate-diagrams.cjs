'use strict';

// Native editable mxGraph cells and SVG previews share the same layout model.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const root = __dirname;
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'icons', 'manifest.json'), 'utf8').replace(/^\uFEFF/, ''));
const icons = Object.fromEntries(manifest.icons.map(entry => {
  const bytes = fs.readFileSync(path.join(root, 'icons', entry.file));
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), entry.sha256, entry.file);
  assert.match(bytes.toString('utf8'), /<svg[\s>]/);
  return [entry.key, bytes.toString('base64')];
}));
const xml = value => String(value).replace(/[<>&"']/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[c]);
const palette = { ink: '#17324d', blue: '#0078d4', teal: '#087f8c', green: '#26723e', red: '#b02e38', gray: '#63758a', purple: '#7252a3' };
const pages = [];
function page(id, name, subtitle, height = 1700) {
  const p = { id, name, subtitle, width: 2400, height, sections: [], nodes: [], edges: [], labels: [] };
  pages.push(p);
  return p;
}
function section(p, id, x, y, w, h, title, fill = '#f3f8fd', stroke = '#bdd5e9') {
  p.sections.push({ id, x, y, w, h, title, fill, stroke });
}
function node(p, id, x, y, w, h, title, lines, icon, options = {}) {
  p.nodes.push({ id, x, y, w, h, title, lines, icon, color: palette.blue, fill: '#ffffff', ...options });
}
function label(p, id, x, y, w, lines, color = palette.gray, size = 17) {
  p.labels.push({ id, x, y, w, lines, color, size });
}
function edge(p, id, source, target, from, to, points = [], color = palette.blue, dashed = false) {
  p.edges.push({ id, source, target, from, to, points, color, dashed });
}
const topology = page('topology', '01 — Private runtime topology',
  'DEPLOYABLE DESIGN • private data plane • Event Grid admission • Durable processing • human release and HL7');
section(topology, 'platform', 35, 160, 510, 1025, 'EXISTING POWER PLATFORM', '#f8f4fc', '#d3c1e9');
section(topology, 'runtime', 575, 160, 1790, 1025, 'AZURE PRIVATE RUNTIME — private endpoints + delegated Function VNet integration');
node(topology, 'power-apps', 65, 240, 450, 140, 'Power Apps', ['User submits fax + source metadata', 'Displays asynchronous job status'], null, { color: palette.purple });
node(topology, 'power-automate', 65, 465, 450, 175, 'Power Automate', ['Existing Power Platform private', 'VNet connectivity; Entra token', 'POST intake / GET job status'], null, { color: palette.purple });
node(topology, 'private-connector', 65, 760, 450, 185, 'Existing private connectivity', ['Environment / connector binding', 'DNS and routes must reach Function PE', 'No public intake endpoint fallback'], 'virtualNetwork', { color: palette.purple });
node(topology, 'ingress-pe', 610, 335, 255, 205, 'Function private\nendpoint', ['HTTPS private ingress', 'sites + SCM DNS', 'Public access disabled'], 'privateEndpoint', { titleSize: 19 });
node(topology, 'function-ingress', 965, 270, 410, 245, 'Azure Functions — intake', ['EasyAuth: single tenant + app roles', 'AzureEventGrid service-tag ingress only', 'Persist original, then ready manifest', '202 + job ID; authenticated status', 'Private API + SCM / VNet route-all', 'Bind source to caller tid:oid'], 'functions');
node(topology, 'jobs', 1495, 285, 360, 220, 'Event Grid + recovery queue', ['BlobCreated: ready/*.json only', 'Direct Durable start in Function', 'Retry 30 times / 24 hours', 'Private dead-letter Blob container', 'fax-jobs recovery + 5-minute repair'], 'queue');
node(topology, 'incoming', 1950, 285, 355, 220, 'Incoming + working', ['incoming: original immutable input', 'working: normalized body artifacts', 'Private data-account blob endpoint', 'No public SAS to callers'], 'blob', { titleSize: 21 });
node(topology, 'worker', 965, 685, 410, 235, 'Functions / Durable target', ['Classify pages → cover handling', 'Classify fax type → extract fields', 'Auto gate OR audited human approval', 'Shared HL7 transform + validation', 'Persist ALL outputs → manifest last', 'Idempotent instance ID = fax ID'], 'functions');
node(topology, 'document-intelligence', 1495, 685, 360, 235, 'Document Intelligence', ['Runtime inference; private endpoint', 'Functions sends document BYTES', 'Separate page + fax-type decisions', 'Exam record / order / Rx / others', 'Type selects schema + extractor'], 'documentIntelligence', { titleSize: 21 });
node(topology, 'business-outputs', 1950, 685, 355, 235, 'Private business\ncontainers', ['human-review: pending, NOT complete', 'working: restricted JSON / HL7 drafts', 'extracted + routed: JSON and HL7', 'Manifest: auto / human provenance', 'HL7 validated before completion'], 'blob', { titleSize: 21 });
node(topology, 'host-storage', 610, 1010, 560, 140, 'SEPARATE Function host storage account', ['Private blob / queue / table / file endpoints', 'Durable state, leases, checkpoints, host content'], 'storage', { titleSize: 20 });
node(topology, 'system-mi', 1215, 1010, 530, 140, 'System-assigned managed identity', ['Scoped business-data + host-storage RBAC', 'Custom DI role: infer + model/classifier metadata'], 'identity', { titleSize: 21 });
node(topology, 'network-contract', 1790, 1010, 515, 140, 'Existing network parameter contract', ['Integration subnet ID ≠ private-endpoint subnet ID', 'Private DNS zone resource IDs supplied as inputs'], 'virtualNetwork', { titleSize: 20 });
edge(topology, 'apps-flow', 'power-apps', 'power-automate', 'bottom', 'top');
edge(topology, 'flow-private-pe', 'power-automate', 'ingress-pe', 'right', 'left', [[560, 552.5], [560, 437.5]]);
edge(topology, 'pe-intake', 'ingress-pe', 'function-ingress', 'right', 'left', [[920, 437.5], [920, 392.5]]);
edge(topology, 'persist-original', 'function-ingress', 'incoming', 'top', 'top', [[1170, 220], [2127.5, 220]]);
edge(topology, 'enqueue', 'function-ingress', 'jobs', 'right', 'left');
edge(topology, 'poll-job', 'jobs', 'worker', 'bottom', 'top', [[1675, 595], [1170, 595]]);
edge(topology, 'read-original', 'incoming', 'worker', 'bottom', 'left', [[2127.5, 635], [900, 635], [900, 802.5]]);
edge(topology, 'analyze-bytes', 'worker', 'document-intelligence', 'right', 'left');
edge(topology, 'publish', 'worker', 'business-outputs', 'bottom', 'bottom', [[1170, 965], [2127.5, 965]]);
label(topology, 'persist-label', 1550, 230, 720, ['1. Persist original bytes, then commit ready manifest'], palette.blue);
label(topology, 'enqueue-label', 1375, 350, 120, ['2. Event'], palette.blue);
label(topology, 'poll-label', 1270, 565, 540, ['3. Event Grid invokes Function; queue is recovery only'], palette.blue);
label(topology, 'read-label', 1600, 613, 440, ['4. Read original over private Blob endpoint'], palette.blue);
label(topology, 'analyze-label', 1375, 748, 120, ['5. Bytes', '+ MI'], palette.blue);
label(topology, 'publish-label', 1420, 941, 520, ['6. Validate required HL7 + all outputs, then commit'], palette.blue);
label(topology, 'platform-note', 70, 1010, 440, ['Power Apps / Automate remain SaaS.', 'Private connectivity applies to workload', 'data ingress, not a claim that the user UI', 'or Entra identity plane is inside this VNet.']);

section(topology, 'operations', 35, 1230, 2330, 370, 'PRIVATE OPERATIONS / OBSERVABILITY — same governed DNS and route reachability', '#f6faf8', '#bcd5c9');
node(topology, 'runner', 65, 1310, 450, 225, 'Private build / deployment runner', ['Prebuild package + Python dependencies', 'Deploy using private Function SCM', 'Runner resolves app + SCM private DNS', 'No public SCM or public data endpoint'], null, { titleSize: 20, color: palette.teal });
node(topology, 'private-dns', 560, 1310, 575, 225, 'Existing private DNS zones', ['azurewebsites.net: app + SCM records', 'blob / queue / table / file.core.windows.net', 'cognitiveservices.azure.com', 'Azure Monitor / Log Analytics private zones', 'All use privatelink zones and private resolution'], 'dns', { color: palette.teal });
node(topology, 'ampls', 1180, 1310, 380, 225, 'Azure Monitor Private Link', ['REQUIRED existing AMPLS + PE', 'Private-only ingestion and query', 'Function telemetry uses private VNet', 'Existing private DNS + scope links'], 'monitor', { titleSize: 21, color: palette.teal });
node(topology, 'app-insights', 1630, 1310, 295, 225, 'Application Insights', ['Workspace-based telemetry', 'Correlated job IDs only', 'No fax payloads in logs', 'Public ingestion disabled'], 'insights', { titleSize: 21, color: palette.teal });
node(topology, 'log-analytics', 1995, 1310, 310, 225, 'Log Analytics', ['Linked workspace', 'Private query / ingestion', 'Operational alerts', 'Existing action group required'], 'logs', { titleSize: 21, color: palette.teal });
edge(topology, 'ampls-insights', 'ampls', 'app-insights', 'right', 'left', [], palette.teal, true);
edge(topology, 'insights-workspace', 'app-insights', 'log-analytics', 'right', 'left', [], palette.teal, true);
label(topology, 'ops-note', 70, 1555, 2220, ['SCM deployments, host-storage access, business-data access, Document Intelligence requests, and telemetry all require private DNS + permitted network routes.'], palette.teal);
label(topology, 'topology-footer', 50, 1630, 2300, ['Blue = private workload data flow. Dashed teal = private monitoring association. Cards describe logical services; PaaS services are reached through private endpoints, not deployed into customer subnets.'], palette.gray, 16);

const workflow = page('workflow', '02 — Fail-closed classification and HL7',
  'TARGET WORKFLOW • new type / human-release / HL7 stages are DESIGN ONLY • pending review or transform failure is NOT completion', 2640);
section(workflow, 'steps', 35, 155, 2330, 2010, 'READ LEFT → RIGHT, THEN FOLLOW ARROWS • both approval paths share the transformation and publication gates');
const card = { titleSize: 22 };
const rejectCard = { ...card, color: palette.red, fill: '#fff5f5' };
const acceptCard = { ...card, color: palette.green, fill: '#f5fcf6' };
node(workflow, 'job-input', 65, 235, 510, 245, '1. Resolve / normalize input', ['Queue contains faxId only; load trusted source', 'Preserve original bytes and page mapping', 'Check media, size, page and parser limits', 'Normalize TIFF; idempotent job checkpoint', 'Invalid input → hold, never complete'], 'queue', card);
node(workflow, 'classify', 650, 235, 510, 245, '2. Classify each page', ['Page-level cover / body classification', 'Document Intelligence REST: split=perPage', 'Every page assigned once; confidence gated', 'Uncertain / missing page labels → review', 'This is NOT the fax-type decision'], 'documentIntelligence', card);
node(workflow, 'cover-decision', 1235, 235, 510, 245, '3. Cover page present?', ['YES: physically remove confident covers', 'NO: retain every body page', 'Preserve original-to-body page mapping', 'Empty body or ambiguous split → review', 'Cover removal is not malware scanning'], 'functions', card);
node(workflow, 'classify-type', 1820, 235, 510, 245, '4. Classify type — NEW', ['Exam record / order / Rx / approved others', 'Classify logical fax using body evidence', 'Known type + calibrated confidence required', 'Unknown / low confidence / mixed → review', 'Select versioned extractor + required schema'], 'documentIntelligence', card);
node(workflow, 'extract', 1820, 650, 510, 265, '5. Extract type-specific content', ['Analyze body bytes, not the cover', 'Use extractor selected by confirmed fax type', 'Required fields, nested items and page map', 'Capture every field value + confidence', 'Type selects downstream HL7 mapping policy'], 'documentIntelligence', card);
node(workflow, 'gate', 1235, 650, 510, 265, '6. Automatic field gate', ['ALL expected fields present and valid', 'ALL scores finite, 0..1 and >= 0.80', '0.80 passes; 0.7999 or missing score fails', 'Minimum score, never average or round up', 'Type / business validation must also pass', 'PASS → automatic approval candidate'], null, { ...card, color: palette.purple, fill: '#f7f1fc' });
node(workflow, 'rejected', 650, 650, 510, 265, 'PendingHumanReview — NOT complete', ['human-review: original + draft + reasons', 'Preserve original scores and type evidence', 'No accepted output or release manifest', 'Unresolved / rejected cases remain held', 'Classification, field or input failures enter here'], 'blob', rejectCard);
node(workflow, 'human-review', 65, 650, 510, 265, '7. Human review — NEW workflow', ['Authorized reviewer verifies the original', 'Confirm fax type, body pages and all fields', 'Correct values; retain original machine scores', 'Re-extract with confirmed type if needed', 'Record reviewer identity, time and evidence', 'No approval until required data is resolved'], null, rejectCard);
node(workflow, 'human-approved', 65, 1060, 510, 245, 'Human-approved candidate — NEW', ['Validate corrected values and required fields', 'Audited approval + version / concurrency check', 'Do NOT raise machine scores to simulate 80%', 'approvalMode=human; original scores retained', 'Still NOT complete; transformation must run'], null, acceptCard);
node(workflow, 'candidate', 650, 1060, 510, 245, '8. Shared approved data contract', ['Merge auto OR human-approved candidates', 'Canonical fields, source and confirmed fax type', 'Track approvalMode=automatic / human', 'Evaluate ALL source / content / type routes', 'Freeze output plan + mapping / profile versions'], 'functions', acceptCard);
node(workflow, 'hl7-required', 1235, 1060, 510, 245, '9. HL7 required by output plan?', ['Per destination + fax type + selected data', 'YES: every required HL7 output must validate', 'Explicit NO: record NotApplicable + policy', 'Missing mapping is an ERROR, not a NO', 'Same decision for auto and human approval'], null, { ...card, color: palette.purple, fill: '#f7f1fc' });
node(workflow, 'hl7-skipped', 1820, 1060, 510, 245, 'Explicitly no HL7 required', ['Versioned policy says NotApplicable', 'Persist the reason in the publication manifest', 'No silent skip for unsupported required data', 'JSON / all other required outputs still apply'], null, acceptCard);
node(workflow, 'transform', 1235, 1460, 510, 270, '10. Transform selected data to HL7', ['Function activity; versioned mapping per route', 'Receiver-approved HL7 version + profile', 'Map IDs, codes, units, timestamps and repeats', 'Escape delimiters; deterministic message IDs', 'Produce restricted working drafts only', 'NO invented / silently truncated clinical values'], 'functions', card);
node(workflow, 'validate-hl7', 650, 1460, 510, 270, '11. Validate ALL required HL7', ['Parse / schema / profile / required segments', 'Semantic checks and source-field reconciliation', 'Patient / encounter / order identity validation', 'Validate every message, not just the first', 'All pass → publish; ANY fail → hold', 'Re-run after every human correction'], 'functions', card);
node(workflow, 'transform-hold', 65, 1460, 510, 270, 'Transform / validation hold', ['Retain draft, mapping version and safe reasons', 'No successful completion or release manifest', 'Retry bounded transient technical failures', 'Data issues → review → approve → transform', 'Mapping defects need an approved code fix', 'Never approve around failed HL7 validation'], 'blob', rejectCard);
node(workflow, 'contract', 1820, 1460, 510, 270, 'FAIL CLOSED — release contract', ['Human review is an approval path, not a bypass', 'Unknown type / missing schema → review', 'Required HL7 unavailable / invalid → hold', 'All approved data + HL7 + routes must persist', 'Consumers use only a completed manifest', 'Population accuracy is evaluated on page 3'], null, { ...card, color: palette.gray, fill: '#f9fafc' });
node(workflow, 'primary-json', 65, 1880, 510, 220, '12. Persist approved primary', ['Store JSON + approval provenance', 'Automatic or explicitly human-reviewed result', 'Immutable, versioned publication identity', 'Not consumable until manifest is committed'], 'blob', acceptCard);
node(workflow, 'routed-blobs', 650, 1880, 510, 220, '13. Persist ALL planned artifacts', ['0..N JSON / selected HL7 route outputs', 'Require every planned write + hash check', 'Partial writes → retry / hold, NOT complete', 'No-match routes: primary is still required'], 'blob', acceptCard);
node(workflow, 'manifest', 1235, 1880, 510, 220, '14. Completion manifest LAST', ['Required HL7 validated OR explicit N/A', 'Primary + ALL planned artifacts committed', 'Record hashes, models, mappings and approval', 'Only now set status=Completed'], 'blob', acceptCard);
node(workflow, 'consumer', 1820, 1880, 510, 220, 'Consumers / delivery boundary', ['Consume only manifest-listed, verified artifacts', 'Blob completion ≠ clinical receiver acceptance', 'If delivery added: track ACK / NACK separately', 'Receiver profile and HL7 version need approval'], null, { ...card, color: palette.teal });
edge(workflow, 'input-classify', 'job-input', 'classify', 'right', 'left');
edge(workflow, 'classify-cover', 'classify', 'cover-decision', 'right', 'left');
edge(workflow, 'cover-type', 'cover-decision', 'classify-type', 'right', 'left');
edge(workflow, 'type-extract', 'classify-type', 'extract', 'bottom', 'top');
edge(workflow, 'extract-gate', 'extract', 'gate', 'left', 'right');
edge(workflow, 'input-review', 'job-input', 'rejected', 'bottom', 'top', [[320, 530], [905, 530]], palette.red, true);
edge(workflow, 'page-review', 'classify', 'rejected', 'bottom', 'top', [], palette.red, true);
edge(workflow, 'cover-review', 'cover-decision', 'rejected', 'bottom', 'top', [[1490, 555], [905, 555]], palette.red, true);
edge(workflow, 'type-review', 'classify-type', 'rejected', 'bottom', 'top', [[2075, 580], [905, 580]], palette.red, true);
edge(workflow, 'gate-reject', 'gate', 'rejected', 'left', 'right', [], palette.red);
edge(workflow, 'review-human', 'rejected', 'human-review', 'left', 'right', [], palette.red);
edge(workflow, 'review-approve', 'human-review', 'human-approved', 'bottom', 'top', [], palette.green);
edge(workflow, 'human-candidate', 'human-approved', 'candidate', 'right', 'left', [], palette.green);
edge(workflow, 'auto-candidate', 'gate', 'candidate', 'bottom', 'top', [[1490, 985], [905, 985]], palette.green);
edge(workflow, 'candidate-plan', 'candidate', 'hl7-required', 'right', 'left', [], palette.green);
edge(workflow, 'hl7-yes', 'hl7-required', 'transform', 'bottom', 'top', [], palette.blue);
edge(workflow, 'hl7-no', 'hl7-required', 'hl7-skipped', 'right', 'left', [], palette.green);
edge(workflow, 'transform-validate', 'transform', 'validate-hl7', 'left', 'right');
edge(workflow, 'transform-error', 'transform', 'transform-hold', 'bottom', 'bottom', [[1490, 1780], [320, 1780]], palette.red, true);
edge(workflow, 'validation-error', 'validate-hl7', 'transform-hold', 'left', 'right', [], palette.red);
edge(workflow, 'hold-review', 'transform-hold', 'human-review', 'left', 'left', [[45, 1595], [45, 782.5]], palette.red);
edge(workflow, 'validated-primary', 'validate-hl7', 'primary-json', 'bottom', 'top', [[905, 1815], [320, 1815]], palette.green);
edge(workflow, 'not-applicable-primary', 'hl7-skipped', 'primary-json', 'right', 'top', [[2350, 1182.5], [2350, 1840], [320, 1840]], palette.green);
edge(workflow, 'primary-routes', 'primary-json', 'routed-blobs', 'right', 'left', [], palette.green);
edge(workflow, 'routes-manifest', 'routed-blobs', 'manifest', 'right', 'left', [], palette.green);
edge(workflow, 'manifest-consumer', 'manifest', 'consumer', 'right', 'left', [], palette.green);
label(workflow, 'classify-failure-label', 1000, 592, 810, ['Dashed red: invalid / uncertain / unknown / mixed → review'], palette.red);
label(workflow, 'automatic-label', 950, 950, 550, ['ALL fields pass → automatic approval'], palette.green);
label(workflow, 'manual-label', 85, 980, 475, ['Verified correction + explicit approval only'], palette.green);
label(workflow, 'yes-label', 1510, 1365, 200, ['YES → transform'], palette.blue);
label(workflow, 'no-label', 1750, 1105, 70, ['NO'], palette.green);
label(workflow, 'valid-label', 680, 1745, 450, ['ALL HL7 valid → publication'], palette.green);
label(workflow, 'publication-note', 70, 2122, 2250, ['No cross-blob transaction. Manifest-last is the release barrier; neither saved review evidence nor a generated HL7 draft means Completed.'], palette.teal);
section(workflow, 'authoring-boundary', 35, 2220, 2330, 315, 'GOVERNED AUTHORING + IMPLEMENTATION BOUNDARY', '#fffaf0', '#e5d2a4');
node(workflow, 'authoring', 65, 2300, 700, 190, 'Separate model authoring', ['Train page classifier, fax-type classifier and extractors', 'Approved training-storage trusted-service exception', 'Evaluate by type/source; promote immutable model IDs', 'Runtime public access remains disabled'], 'documentIntelligence', { color: '#916719', titleSize: 22 });
node(workflow, 'mapping-governance', 850, 2300, 710, 190, 'HL7 and human-release governance — NEW', ['Receiver agrees message version, profile and field mappings', 'Clinical owner approves codes, units and identity matching', 'Review service requires RBAC, immutable audit and concurrency', 'Both paths must pass the same transformation checks'], null, { color: '#916719', titleSize: 21 });
node(workflow, 'implementation-boundary', 1645, 2300, 660, 190, 'DESIGN ≠ current runtime', ['Existing app has page-derived type + 80% gate + JSON routes.', 'Dedicated type step, human release and HL7 are not coded.', 'Do not deploy this diagram as proof of those features.', 'See success-criteria.md and architecture README.'], null, { color: '#916719', titleSize: 22 });
label(workflow, 'workflow-footer', 50, 2580, 2280, ['Proposed target workflow, not a measured accuracy or clinical-safety claim. A failed required check always blocks completion.'], palette.gray, 18);

const criteria = page('criteria', '03 — Success criteria matrix',
  'PROPOSED RELEASE TARGETS • NOT measured results • all mandatory gates must pass; insufficient evidence = NOT QUALIFIED', 1880);
section(criteria, 'matrix', 35, 160, 2330, 1500, 'Evaluate a frozen version on independently adjudicated, held-out faxes; stratify by type, source, quality and approval path');
const metrics = [
  ['A. Fax-type classification', ['Macro-F1 >= 0.98 on supported fax types', 'Recall >= 0.97 for EACH supported type', 'Unknown / mixed detection recall >= 0.99', 'Minimum 200 held-out faxes per supported type', 'Include exam record, order and Rx separately']],
  ['B. Cover / page preservation', ['Cover-removal precision >= 0.999', 'Cover-removal recall >= 0.99', 'ZERO clinically relevant body pages removed', 'Test cover, no-cover, repeats and poor scans', 'Any harmful deletion blocks release']],
  ['C. Automatic fax correctness', ['All-required-fields-correct fax rate:', 'one-sided 95% lower bound >= 0.99', 'Denominator: independently audited AUTO releases', 'Never average field accuracy across a fax', 'Evaluate each enabled type / source stratum']],
  ['D. Critical clinical error gate', ['ZERO observed critical released-data errors', 'One-sided 95% error upper bound <= 0.001', 'At least 2,995 audited releases if zero errors', 'IDs, drug / dose / unit, order / result semantics', 'Assess automatic and human paths separately']],
  ['E. Human-reviewed correctness', ['All-required-fields-correct fax rate:', 'one-sided 95% lower bound >= 0.99', 'Independent second audit, NOT self-approval', '100% reviewer identity / time / evidence audit', 'Original machine scores remain unchanged']],
  ['F. Fail-closed invariants', ['100% negative test cases block completion', 'Missing / <0.80 fields never auto-release', 'Unknown type / failed HL7 never completes', 'Human approval cannot bypass HL7 checks', 'ZERO unauthorized or duplicate releases']],
  ['G. HL7 fidelity / conformance', ['100% required outputs parse + match profile', '100% selected-field reconciliation on gold set', 'ZERO critical code / unit / identity mismatches', 'Every destination + version + repeat tested', 'Invalid mappings or missing data → hold']],
  ['H. Publication reliability', ['>= 99.9% eligible jobs complete within 15 min', 'From upload, or human approval for review path', 'All output hashes + manifest reconcile: 100%', 'Fault / restart / duplicate / timeout test loss: 0', 'Required HL7 failures remain in denominator']],
  ['I. Latency / review operations', ['Automatic completion latency: p95 <= 5 min', 'Measured at agreed peak load; errors count', '>= 95% review cases resolved within 1 workday', 'Report queue age, unresolved cases and N/A rate', 'Human wait is separate from compute latency']],
  ['J. Automation / rejection burden', ['Proposed straight-through release rate >= 70%', 'Denominator: all valid supported input faxes', 'Report by type / source, include rejected reads', 'Never lower 80% or safety gates to hit yield', 'Capacity-plan the remaining review workload']],
  ['K. Evidence and drift', ['>= 30-day pilot at agreed peak load', 'Confidence bounds, counts and versions recorded', 'Critical error: immediately hold affected releases', 'Any gate miss / weak evidence: not qualified', 'Requalify model, schema and mapping changes']],
  ['L. Final release decision', ['Clinical + data + interface + operations sign-off', 'ALL mandatory criteria pass; no weighted average', 'Unmeasured target is NOT an achieved result', 'Docs define denominators and statistical tests', 'Blob completion is NOT receiver ACK / acceptance']]
];
metrics.forEach(([title, lines], index) => node(criteria, `metric-${index}`, 65 + (index % 3) * 775,
  240 + Math.floor(index / 3) * 345, 745, 295, title, lines, null,
  { titleSize: 23, color: index === 3 || index === 5 ? palette.red : palette.teal, fill: '#ffffff' }));
label(criteria, 'matrix-note', 65, 1695, 2240, ['Accuracy is checked against independent ground truth, not the model confidence score. The fixed 0.80 gate alone does not prove correctness.',
  'Targets are proposed for owner approval. Confidence intervals apply to rates, not F1; see ../docs/success-criteria.md for sampling and formulas.',
  'Critical-error bound with zero errors: U95 = 1 - 0.05^(1/n). Do not claim a per-type/source result from a pooled sample.',
  'Current package has no dedicated type classifier, HL7 transform or human-release API. These target stages need implementation and validation.'], palette.gray, 20);

const reporting = page('reporting', '04 — Reporting metrics and operational insights',
  'PROPOSED REPORTING DESIGN • no live dashboard or new instrumentation deployed • targets reference page 3; no fabricated measurements', 2120);
section(reporting, 'reporting-matrix', 35, 160, 2330, 1620, 'HIGH-VALUE METRICS • show counts + denominators + trends + target + evidence status, not a single composite score');
const reportingMetrics = [
  ['R1. Demand and throughput', [
    'KPIs: unique uploads, pages, completed faxes / hour',
    'Breakout: fax type, source, file format, page count',
    'Show arrivals vs completions and open work trend',
    'Source: intake ledger + committed manifests',
    'Cadence: 5-min operations; daily capacity trend',
    'Action: sustained arrivals > completions → capacity',
    'Count business faxes, not retry events'
  ]],
  ['R2. Outcome funnel and automation [J]', [
    'Upload → type → field gate → HL7 → completed',
    'Auto releases / valid supported inputs; target >= 70%',
    'Separate human releases, held, failed and rejected',
    'Breakout: type / source / model / approval mode',
    'Source: deduplicated job transitions + manifests',
    'Daily mature cohorts; show pending separately',
    'Never hide rejects or reduce 80% to improve yield'
  ]],
  ['R3. Audited accuracy / critical errors [C,D,E]', [
    'All-required-fields-correct faxes / audited releases',
    'Lower 95% bound >= 99%; auto and human separate',
    'Critical errors: 0; upper 95% bound <= 0.1%',
    'Breakout: type, source, approval and model version',
    'Source: independent adjudicated audit, NOT scores',
    'Weekly audit / 30-day trend; incident immediately',
    'Show n + bounds; under-sampled ≠ qualified'
  ]],
  ['R4. Type and cover accuracy [A,B]', [
    'Type confusion matrix, macro-F1 and per-type recall',
    'Unknown / mixed rate AND audited detection recall',
    'Cover precision / recall; harmful page removals: 0',
    'Breakout: exam / order / Rx, source and scan quality',
    'Source: classifier decisions + labeled audit samples',
    'Weekly + every model release; compare to page 3',
    'Any harmful deletion → hold affected releases'
  ]],
  ['R5. Required-field rejection drivers [F]', [
    'Faxes failing field gate / faxes reaching that gate',
    'Top missing, unreadable, invalid and <0.80 reasons',
    'Field missing rate + minimum-confidence histogram',
    'Breakout: schema field ID / type / source / version',
    'Source: safe gate codes; no extracted values',
    'Daily Pareto trend; report multi-reason overlap',
    'Prioritize labeling / schema fixes, not lower scores'
  ]],
  ['R6. Review backlog and effectiveness [E,I]', [
    'Open cases, oldest age, arrivals vs resolutions',
    '>= 95% resolved within 1 working day; pending count',
    'Queue wait vs active handling time; rework rate',
    'Breakout: type / reason / team / source; no names',
    'Source: review case events + independent audit',
    'Hourly operations; daily SLA / staffing report',
    'Resolution ≠ approval ≠ successful publication'
  ]],
  ['R7. HL7 transformation and fidelity [G]', [
    'First-pass valid / required-HL7 fax revisions',
    'All planned messages must pass before completion',
    'By mapping / profile / destination / approval mode',
    'Track invalid codes, units, IDs and missing mappings',
    'Source: validator summary + golden-source audit',
    '5-min failures; daily trends; 100% conformance gate',
    'Explicit N/A separate; receiver ACK not assumed'
  ]],
  ['R8. Publication reliability [F,H]', [
    '>= 99.9% eligible jobs complete within 15 minutes',
    'Oldest queue age, retries, poison and stuck stages',
    'Manifest reconciliation 100%; loss / duplicates: 0',
    'Breakout: stage / dependency / route / deployment',
    'Source: ledger, queue signals + artifact reconciliation',
    '5-min operations; daily deadline-cohort SLO',
    'Failed required HL7 stays in eligible denominator'
  ]],
  ['R9. Latency and bottlenecks [I]', [
    'Auto upload-to-manifest p50 / p95 / p99; p95 <= 5 min',
    'Review approval-to-manifest separate from human wait',
    'Stage queue / classify / extract / HL7 / write timings',
    'Breakout: pages / format / type / load / approval mode',
    'Source: correlated milestone timestamps',
    '5-min operations; daily percentiles + SLA miss rate',
    'Do not drop timed-out, failed or still-pending jobs'
  ]],
  ['R10. Fail-closed / privacy / audit [F,L]', [
    'Unauthorized release, bypass or data loss: 0',
    '100% human approval + mapping provenance coverage',
    'Denied requests separate from successful misuse',
    'Breakout: operation / reason / environment only',
    'Source: access decisions + release/audit reconciliation',
    'Immediate critical incident; daily control summary',
    'No PHI, raw fax text, HL7 payloads or secrets in BI'
  ]],
  ['R11. Cost and business value', [
    'Cloud cost / completed fax; cost / input page',
    'Separate cloud spend, review labor and retry cost',
    'Breakout: environment / period / type estimate',
    'Source: billing allocation + measured handling time',
    'Daily provisional; monthly reconciled with Finance',
    'Budget alert owner-approved; no invented cost target',
    'Allocated costs labeled; baseline needed for savings'
  ]],
  ['R12. Drift and reporting trust [K,L]', [
    'Model / schema / mapping versions + audit coverage',
    'Source/type mix, rejects and confidence distribution',
    'Freshness lag, missing events and reconciliation gap',
    'Source: ledger + telemetry + audit version registry',
    'Daily trends / weekly quality / monthly governance',
    'Stale or missing data = Unknown, never green / zero',
    'Critical error → hold; changes require requalification'
  ]]
];
reportingMetrics.forEach(([title, lines], index) => node(reporting, `report-${index + 1}`,
  65 + (index % 3) * 775, 240 + Math.floor(index / 3) * 375, 745, 335, title, lines, null,
  { titleSize: 23, color: [2, 9].includes(index) ? palette.red : palette.teal, fill: '#ffffff' }));
label(reporting, 'reporting-footer', 65, 1820, 2250, [
  'GLOBAL FILTERS: UTC cohort / environment / fax type (including unknown) / approved source ID / auto vs human / model-schema-mapping / destination.',
  'EXECUTIVE: volume, yield, accuracy, SLA, cost. OPERATIONS: backlog, stage latency, HL7 failures, retries. QUALITY: audits, error bounds and drift.',
  'Proposed read-only reporting path: job/manifest facts + safe telemetry + independent audits + billing → governed aggregates → role-scoped dashboard.',
  'Use unsampled reconciled records for business totals. Retries, multiple routes and corrected revisions must not inflate unique-fax success counts.',
  'All targets remain proposed. Page 3 defines release qualification; this page defines monitoring. See ../docs/reporting-metrics.md for metric formulas.',
  'New HL7 / review / type and reporting stages require implementation. No live metrics, clinical data or monitoring resources were accessed.'
], palette.gray, 19);

function point(n, side) {
  return ({ left: [n.x, n.y + n.h / 2], right: [n.x + n.w, n.y + n.h / 2], top: [n.x + n.w / 2, n.y], bottom: [n.x + n.w / 2, n.y + n.h] })[side];
}
function validate(p) {
  const items = [...p.sections, ...p.nodes, ...p.labels, ...p.edges];
  assert.equal(new Set(items.map(n => n.id)).size, items.length, 'Duplicate IDs');
  const ids = new Set(p.nodes.map(n => n.id));
  for (const e of p.edges) {
    assert(ids.has(e.source) && ids.has(e.target), `Broken edge ${e.id}`);
    assert(point(p.nodes.find(n => n.id === e.source), e.from));
  }
  for (const n of [...p.sections, ...p.nodes]) {
    assert(n.x >= 0 && n.y >= 0 && n.x + n.w <= p.width && n.y + n.h <= p.height, `Out-of-bounds ${n.id}`);
    if (n.icon) assert(icons[n.icon], `Missing icon ${n.icon}`);
  }
}
function geometry(x, y, width, height) {
  return `<mxGeometry x="${x}" y="${y}" width="${width}" height="${height}" as="geometry"/>`;
}
function textCell(id, parent, x, y, w, h, lines, size, color, bold = false, align = 'left', fill = 'none') {
  return `<mxCell id="${id}" value="${xml(lines.join('\n'))}" style="text;html=0;whiteSpace=wrap;overflow=hidden;align=${align};verticalAlign=middle;spacing=0;fontFamily=Arial;fontSize=${size};fontColor=${color};fontStyle=${bold ? 1 : 0};fillColor=${fill};strokeColor=none;" vertex="1" parent="${parent}">${geometry(x, y, w, h)}</mxCell>`;
}
function drawio(p) {
  const cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>'];
  cells.push(textCell(`${p.id}-heading`, '1', 45, 34, 2300, 50, [p.name], 38, palette.ink, true));
  cells.push(textCell(`${p.id}-subtitle`, '1', 48, 95, 2300, 36, [p.subtitle], 20, palette.gray));
  for (const s of p.sections) {
    cells.push(`<mxCell id="${s.id}" style="rounded=1;arcSize=3;fillColor=${s.fill};strokeColor=${s.stroke};strokeWidth=2;connectable=0;" vertex="1" parent="1">${geometry(s.x, s.y, s.w, s.h)}</mxCell>`);
    cells.push(textCell(`${s.id}-title`, s.id, 20, 14, s.w - 40, 34, [s.title], 22, palette.ink, true));
  }
  for (const e of p.edges) {
    const ports = { left: [0, 0.5], right: [1, 0.5], top: [0.5, 0], bottom: [0.5, 1] };
    const [ex, ey] = ports[e.from], [ix, iy] = ports[e.to];
    cells.push(`<mxCell id="${e.id}" style="edgeStyle=none;rounded=0;html=0;endArrow=block;endFill=1;strokeWidth=2.5;strokeColor=${e.color};dashed=${e.dashed ? 1 : 0};exitX=${ex};exitY=${ey};exitPerimeter=1;entryX=${ix};entryY=${iy};entryPerimeter=1;" edge="1" source="${e.source}" target="${e.target}" parent="1"><mxGeometry relative="1" as="geometry">${e.points.length ? `<Array as="points">${e.points.map(([x, y]) => `<mxPoint x="${x}" y="${y}"/>`).join('')}</Array>` : ''}</mxGeometry></mxCell>`);
  }
  for (const n of p.nodes) {
    cells.push(`<mxCell id="${n.id}" value="" style="${n.diamond ? 'rhombus' : 'rounded=1;arcSize=8'};fillColor=${n.fill};strokeColor=${n.color};strokeWidth=2;" vertex="1" parent="1">${geometry(n.x, n.y, n.w, n.h)}</mxCell>`);
    if (n.icon) {
      // mxGraph styles separate tokens with semicolons: its SVG image convention omits ";base64".
      cells.push(`<mxCell id="${n.id}-icon" style="shape=image;imageAspect=1;aspect=fixed;image=data:image/svg+xml,${icons[n.icon]};" vertex="1" parent="${n.id}">${geometry(18, 17, 43, 43)}</mxCell>`);
    }
    const tx = n.icon ? 75 : 20, titleY = n.diamond ? 77 : 14;
    cells.push(textCell(`${n.id}-title`, n.id, n.diamond ? 35 : tx, titleY, n.w - (n.diamond ? 70 : tx + 15), 48, n.title.split('\n'), n.titleSize || 23, palette.ink, true, n.diamond ? 'center' : 'left'));
    cells.push(textCell(`${n.id}-body`, n.id, n.diamond ? 40 : 20, n.diamond ? 130 : 70, n.w - (n.diamond ? 80 : 40), n.lines.length * 25, n.lines, 18, palette.ink, false, n.diamond ? 'center' : 'left'));
  }
  for (const t of p.labels) cells.push(textCell(t.id, '1', t.x, t.y, t.w, t.lines.length * (t.size + 6), t.lines, t.size, t.color));
  return `<diagram id="${p.id}" name="${xml(p.name)}"><mxGraphModel dx="2400" dy="${p.height}" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="${p.width}" pageHeight="${p.height}" math="0" shadow="0"><root>${cells.join('\n')}</root></mxGraphModel></diagram>`;
}
function svgText(x, y, lines, size = 18, color = palette.ink, bold = false, anchor = 'start') {
  return `<text x="${x}" y="${y}" fill="${color}" font-size="${size}" font-weight="${bold ? 700 : 400}" text-anchor="${anchor}">${lines.map((line, i) => `<tspan x="${x}" dy="${i ? 25 : 0}">${xml(line)}</tspan>`).join('')}</text>`;
}
function svg(p) {
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" width="${p.width}" height="${p.height}" viewBox="0 0 ${p.width} ${p.height}" role="img" aria-labelledby="title desc"><title id="title">${xml(p.name)}</title><desc id="desc">${xml(p.subtitle)}. All service icons are unmodified embedded official Microsoft Azure SVGs.</desc><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs><g font-family="Arial, sans-serif"><rect width="100%" height="100%" fill="#ffffff"/>`];
  parts.push(svgText(45, 75, [p.name], 38, palette.ink, true), svgText(48, 119, [p.subtitle], 20, palette.gray));
  for (const s of p.sections) {
    parts.push(`<rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="12" fill="${s.fill}" stroke="${s.stroke}" stroke-width="2"/>`, svgText(s.x + 20, s.y + 38, [s.title], 22, palette.ink, true));
  }
  for (const e of p.edges) {
    const pts = [point(p.nodes.find(n => n.id === e.source), e.from), ...e.points, point(p.nodes.find(n => n.id === e.target), e.to)];
    parts.push(`<polyline id="${e.id}" points="${pts.map(pt => pt.join(',')).join(' ')}" fill="none" stroke="${e.color}" stroke-width="2.5" ${e.dashed ? 'stroke-dasharray="9 6"' : ''} marker-end="url(#arrow)"/>`);
  }
  for (const n of p.nodes) {
    parts.push(`<g id="${n.id}">`);
    if (n.diamond) {
      parts.push(`<polygon points="${n.x + n.w / 2},${n.y} ${n.x + n.w},${n.y + n.h / 2} ${n.x + n.w / 2},${n.y + n.h} ${n.x},${n.y + n.h / 2}" fill="${n.fill}" stroke="${n.color}" stroke-width="2"/>`);
    } else parts.push(`<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="12" fill="${n.fill}" stroke="${n.color}" stroke-width="2"/>`);
    if (n.icon) parts.push(`<image x="${n.x + 18}" y="${n.y + 17}" width="43" height="43" preserveAspectRatio="xMidYMid meet" href="data:image/svg+xml;base64,${icons[n.icon]}"/>`);
    parts.push(svgText(n.diamond ? n.x + n.w / 2 : n.x + (n.icon ? 75 : 20), n.y + (n.diamond ? 109 : n.title.includes('\n') ? 33 : 46), n.title.split('\n'), n.titleSize || 23, palette.ink, true, n.diamond ? 'middle' : 'start'));
    parts.push(svgText(n.diamond ? n.x + n.w / 2 : n.x + 20, n.y + (n.diamond ? 155 : 92), n.lines, 18, palette.ink, false, n.diamond ? 'middle' : 'start'));
    parts.push('</g>');
  }
  for (const t of p.labels) parts.push(svgText(t.x, t.y + t.size, t.lines, t.size, t.color));
  parts.push('</g></svg>');
  return parts.join('\n');
}
pages.forEach(validate);
// Enforce the proposed release barriers as graph contracts, not labels alone.
const predecessors = id => workflow.edges.filter(e => e.target === id).map(e => e.source).sort();
assert.deepEqual(predecessors('extract'), ['classify-type']);
assert.deepEqual(predecessors('candidate'), ['gate', 'human-approved']);
assert.deepEqual(predecessors('transform'), ['hl7-required']);
assert.deepEqual(predecessors('primary-json'), ['hl7-skipped', 'validate-hl7']);
assert.deepEqual(predecessors('manifest'), ['routed-blobs']);
assert.deepEqual(predecessors('consumer'), ['manifest']);
assert(!workflow.edges.some(e => e.source === 'rejected' && e.target === 'manifest'));
const drawioXml = `<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="app.diagrams.net" agent="local-generator" version="24.7.17" type="device" compressed="false">${pages.map(drawio).join('\n')}</mxfile>\n`;
fs.writeFileSync(path.join(root, 'Azure-Fax-Production.drawio'), drawioXml);
// Keep the originally requested drawing synchronized when its folder is present.
const originalFolder = path.resolve(root, '..', '..', 'Azure-Fax-Architecture');
if (fs.existsSync(originalFolder)) fs.writeFileSync(path.join(originalFolder, 'azure-fax-processing.drawio.xml'), drawioXml);
for (const p of pages) fs.writeFileSync(path.join(root, `${p.id}.svg`), svg(p));
fs.writeFileSync(path.join(root, 'preview.html'), `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Azure Fax Production — Architecture</title><style>
body{margin:0;padding:24px;background:#e9eef4;color:#17324d;font-family:Arial,sans-serif}
header,main{max-width:1800px;margin:auto}a{color:#005a9e}nav{margin:16px 0}
article{background:white;margin:24px 0;padding:12px;box-shadow:0 3px 15px #17324d22}
svg{display:block;width:100%;height:auto}h1{font-size:26px}
</style></head><body><header><h1>Azure Fax Production — Private Architecture</h1>
<p>Deployable architecture: Event Grid admission, dedicated fax-type classification, human-reviewed release and HL7 validation. Targets are not measured results. All icons are embedded; no remote resources or scripts.</p>
<nav><a href="Azure-Fax-Production.drawio">Editable draw.io XML</a> · <a href="topology.svg">Topology SVG</a> · <a href="workflow.svg">Workflow SVG</a> · <a href="criteria.svg">Success matrix SVG</a> · <a href="reporting.svg">Reporting metrics SVG</a> · <a href="../docs/reporting-metrics.md">Metric definitions</a> · <a href="../docs/success-criteria.md">Detailed criteria</a> · <a href="README.md">Architecture guide</a></nav></header>
<main>${pages.map(p => `<article aria-label="${xml(p.name)}">${svg(p).replaceAll('id="arrow"', `id="${p.id}-arrow"`).replaceAll('url(#arrow)', `url(#${p.id}-arrow)`).replaceAll('id="title"', `id="${p.id}-title"`).replaceAll('id="desc"', `id="${p.id}-desc"`).replaceAll('aria-labelledby="title desc"', `aria-labelledby="${p.id}-title ${p.id}-desc"`)}</article>`).join('\n')}</main></body></html>`);
console.log(JSON.stringify({ pages: pages.map(p => ({ name: p.name, nodes: p.nodes.length, edges: p.edges.length, icons: p.nodes.filter(n => n.icon).length })), officialIconsVerified: manifest.icons.length }, null, 2));
