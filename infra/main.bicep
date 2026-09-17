targetScope = 'resourceGroup'

@description('Lowercase letters, digits and internal hyphens; start with a letter. Use a dedicated resource group.')
@minLength(3)
@maxLength(12)
param namePrefix string
@description('Optional clean-host replacement name; reuses the original plan and private storage.')
param functionAppNameOverride string = ''
param deploymentPackageUrl string = ''
@description('Set false only when equivalent pre-existing runtime role assignments were verified independently.')
param deployRuntimeRoleAssignments bool = true

@description('Azure public-cloud region, matching the supplied VNet and available Linux Elastic Premium/Document Intelligence capacity.')
param location string

@description('Defaults to the deployment subscription tenant; must match the single-tenant API registration.')
param tenantId string = subscription().tenantId

@description('Existing Entra API application client ID; configure requestedAccessTokenVersion=2 and application roles separately.')
@minLength(36)
@maxLength(36)
param apiClientId string

@description('Required nonempty list of approved calling application CLIENT IDs, not enterprise application object IDs.')
@minLength(1)
param allowedClientApplicationIds array

@description('Optional oid allowlist; up to 13 GUIDs to stay within EasyAuth 500-character limit. Combined with client allowlist by AND.')
@maxLength(13)
param allowedPrincipalObjectIds array = []

@description('Existing Microsoft.Web/serverFarms-delegated integration subnet. Separate from private endpoint subnet.')
param integrationSubnetId string
param privateEndpointSubnetId string

@sealed()
type privateDnsIds = {
  blob: string
  queue: string
  table: string
  file: string
  sites: string
  cognitiveServices: string
}

@description('Existing, VNet-linked private DNS zone IDs. No shared VNet, subnet, or DNS link is created or modified.')
param privateDnsZoneIds privateDnsIds

@description('Existing AMPLS with approved private endpoint, PrivateOnly access modes and required DNS already configured. New monitoring resources are linked to it.')
param existingAmplsResourceId string

@description('Existing, monitored action group resource ID; required so alerts cannot be silently omitted.')
param actionGroupResourceId string

@allowed(['EP1', 'EP2', 'EP3'])
param premiumSku string = 'EP1'
@minValue(1)
@maxValue(20)
param minimumInstanceCount int = 1
@minValue(1)
@maxValue(20)
param maximumInstanceCount int = 5
@allowed(['Standard_LRS', 'Standard_ZRS'])
param storageSku string = 'Standard_ZRS'
@minValue(30)
@maxValue(730)
param logRetentionInDays int = 30
@minValue(1)
param logDailyQuotaGb int = 5
@minValue(1)
param http5xxThreshold int = 5
@minValue(1)
param queuedMessageThreshold int = 100
@description('Must remain false for initial deployment. Enabling admission is a separate validated release gate.')
param processingEnabled bool = false
param tags object = {
  application: 'private-fax-processing'
  dataClassification: 'sensitive'
}

var suffix = uniqueString(resourceGroup().id, namePrefix)
var originalAppName = 'func-${namePrefix}-${suffix}'
var appName = empty(functionAppNameOverride) ? originalAppName : functionAppNameOverride
var commonTags = union(tags, { workload: namePrefix })
var amplsParts = split(existingAmplsResourceId, '/')

resource runtimeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${namePrefix}-${suffix}-runtime'
  location: location
  tags: commonTags
}

module host 'modules/storage.bicep' = {
  name: 'fax-host-storage'
  params: {
    name: 'stfaxhost${suffix}'
    location: location
    isHost: true
    privateEndpointSubnetId: privateEndpointSubnetId
    privateDnsZoneIds: privateDnsZoneIds
    skuName: storageSku
    tags: commonTags
  }
}

module data 'modules/storage.bicep' = {
  name: 'fax-data-storage'
  params: {
    name: 'stfaxdata${suffix}'
    location: location
    isHost: false
    privateEndpointSubnetId: privateEndpointSubnetId
    privateDnsZoneIds: privateDnsZoneIds
    skuName: storageSku
    tags: commonTags
  }
}

module documentIntelligence 'modules/document-intelligence.bicep' = {
  name: 'fax-document-intelligence'
  params: {
    name: 'di-${namePrefix}-${suffix}'
    location: location
    privateEndpointSubnetId: privateEndpointSubnetId
    privateDnsZoneId: privateDnsZoneIds.cognitiveServices
    tags: commonTags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'fax-monitoring'
  params: {
    name: '${namePrefix}-${suffix}'
    location: location
    tags: commonTags
    retentionInDays: logRetentionInDays
    dailyQuotaGb: logDailyQuotaGb
  }
}

module monitoringLinks 'modules/ampls-links.bicep' = {
  name: 'fax-monitoring-links-${suffix}'
  scope: resourceGroup(amplsParts[2], amplsParts[4])
  params: {
    amplsName: amplsParts[8]
    workspaceId: monitoring.outputs.workspaceId
    insightsId: monitoring.outputs.insightsId
  }
}

module functions 'modules/functions.bicep' = {
  name: 'fax-functions'
  params: {
    name: appName
    planName: 'plan-${originalAppName}'
    location: location
    tags: commonTags
    integrationSubnetId: integrationSubnetId
    privateEndpointSubnetId: privateEndpointSubnetId
    privateDnsZoneId: privateDnsZoneIds.sites
    hostBlobUrl: host.outputs.blobUrl
    deploymentPackageUrl: deploymentPackageUrl
    hostQueueUrl: host.outputs.queueUrl
    hostTableUrl: host.outputs.tableUrl
    dataBlobUrl: data.outputs.blobUrl
    dataQueueUrl: data.outputs.queueUrl
    documentIntelligenceEndpoint: documentIntelligence.outputs.endpoint
    applicationInsightsConnectionString: monitoring.outputs.connectionString
    runtimeIdentityResourceId: runtimeIdentity.id
    runtimeIdentityClientId: runtimeIdentity.properties.clientId
    tenantId: tenantId
    apiClientId: apiClientId
    allowedClientApplicationIds: allowedClientApplicationIds
    allowedPrincipalObjectIds: allowedPrincipalObjectIds
    processingEnabled: processingEnabled
    premiumSku: premiumSku
    maximumInstanceCount: maximumInstanceCount
    minimumInstanceCount: minimumInstanceCount
  }
  dependsOn: [monitoringLinks]
}

module runtimeRbac 'modules/runtime-rbac.bicep' = {
  name: 'fax-runtime-rbac'
  params: {
    principalId: runtimeIdentity.properties.principalId
    hostStorageName: host.outputs.accountName
    dataStorageName: data.outputs.accountName
    documentIntelligenceName: documentIntelligence.outputs.name
    insightsName: monitoring.outputs.insightsName
    deployAssignments: deployRuntimeRoleAssignments
  }
}

module eventGrid 'modules/event-grid.bicep' = {
  name: 'fax-ready-event-grid'
  params: {
    name: 'ready-blob-created'
    storageAccountId: data.outputs.id
    functionAppId: functions.outputs.id
  }
  dependsOn: [runtimeRbac]
}

module alerts 'modules/alerts.bicep' = {
  name: 'fax-alerts'
  params: {
    name: namePrefix
    location: location
    tags: commonTags
    functionAppId: functions.outputs.id
    queueServiceId: data.outputs.queueServiceId
    actionGroupResourceId: actionGroupResourceId
    http5xxThreshold: http5xxThreshold
    queuedMessageThreshold: queuedMessageThreshold
  }
}

module workbooks 'modules/workbooks.bicep' = {
  params: {
    name: namePrefix
    location: location
    tags: commonTags
    applicationInsightsResourceId: monitoring.outputs.insightsId
    logAnalyticsWorkspaceResourceId: monitoring.outputs.workspaceId
  }
}

// Only identifiers and non-secret service URLs may be deployment outputs.
output functionAppName string = functions.outputs.name
output functionAppResourceId string = functions.outputs.id
output runtimePrincipalId string = runtimeIdentity.properties.principalId
output runtimeIdentityClientId string = runtimeIdentity.properties.clientId
output runtimeIdentityResourceId string = runtimeIdentity.id
output apiBaseUrl string = functions.outputs.apiBaseUrl
output scmHostName string = functions.outputs.scmHostName
output dataStorageAccountName string = data.outputs.accountName
output hostStorageAccountName string = host.outputs.accountName
output dataStorageAccountUrl string = data.outputs.blobUrl
output dataQueueUrl string = data.outputs.queueUrl
output documentIntelligenceEndpoint string = documentIntelligence.outputs.endpoint
output documentIntelligenceResourceId string = documentIntelligence.outputs.id
output documentIntelligenceRuntimeRoleDefinitionId string = runtimeRbac.outputs.documentIntelligenceRuntimeRoleDefinitionId
output eventGridSubscriptionId string = eventGrid.outputs.id
output eventGridSubscriptionName string = eventGrid.outputs.name
output applicationInsightsResourceId string = monitoring.outputs.insightsId
output logAnalyticsWorkspaceResourceId string = monitoring.outputs.workspaceId
output operationsWorkbookResourceId string = workbooks.outputs.operationsWorkbookId
output qualityWorkbookResourceId string = workbooks.outputs.qualityWorkbookId
output jobQueueName string = 'fax-jobs'
output poisonQueueName string = 'fax-jobs-poison'
output readyContainerName string = 'ready'
output privateEndpointIds array = concat(host.outputs.privateEndpointIds, data.outputs.privateEndpointIds, [functions.outputs.privateEndpointId, documentIntelligence.outputs.privateEndpointId])
