targetScope = 'subscription'
@allowed(['westus2'])
param location string = 'westus2'
@minLength(3)
@maxLength(12)
param namePrefix string = 'faxe2e'
param functionAppNameOverride string = ''
param deploymentPackageUrl string = ''
@description('Set false only for an existing test environment whose runtime role assignments were verified.')
param deployRuntimeRoleAssignments bool = true
param runtimeResourceGroupName string = 'rg-fax-e2e-westus2'
param authoringResourceGroupName string = 'rg-fax-authoring-e2e-westus2'
@description('Existing or separately approved isolated single-tenant API registration. No directory resources are created by Bicep.')
param apiClientId string
@description('Separately approved delegated human-review client application IDs. Empty keeps the test MI-only. This grants no directory consent or app role.')
@maxLength(5)
param humanReviewClientApplicationIds string[] = []
@description('Separately approved human reviewer object IDs; pair with their delegated client IDs and actual Fax.Review assignments.')
@maxLength(10)
param humanReviewPrincipalObjectIds string[] = []
@description('SSH public key only; runner has no inbound connectivity and is accessed with ARM RunCommand.')
param runnerSshPublicKey string
@allowed(['Standard_LRS', 'StandardSSD_LRS'])
param runnerOsDiskStorageAccountType string = 'StandardSSD_LRS'
@description('Monitored test-owner address. Alert payloads must never include document content.')
param alertEmail string
@minValue(1)
@maxValue(2)
param maximumInstanceCount int = 1
param tags object = {
  application: 'private-fax-test'
  dataClassification: 'synthetic-only'
  testSpendLimitUsd: '100-stop-and-ask'
}
// Direct RG declarations provide compile-time scopes for existing reusable modules.
resource runtimeGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: runtimeResourceGroupName
  location: location
  tags: tags
}
resource authoringGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: authoringResourceGroupName
  location: location
  tags: tags
}
module foundation 'modules/test-foundation.bicep' = {
  name: 'fax-test-foundation'
  scope: runtimeGroup
  params: {
    namePrefix: namePrefix
    location: location
    runnerSshPublicKey: runnerSshPublicKey
    runnerOsDiskStorageAccountType: runnerOsDiskStorageAccountType
    alertEmail: alertEmail
    tags: tags
  }
}
module runtime 'main.bicep' = {
  name: 'fax-test-runtime'
  scope: runtimeGroup
  params: {
    namePrefix: namePrefix
    functionAppNameOverride: functionAppNameOverride
    deploymentPackageUrl: deploymentPackageUrl
    location: location
    apiClientId: apiClientId
    allowedClientApplicationIds: concat([foundation.outputs.identities[0].clientId, foundation.outputs.identities[1].clientId], humanReviewClientApplicationIds)
    allowedPrincipalObjectIds: concat([foundation.outputs.identities[0].principalId, foundation.outputs.identities[1].principalId], humanReviewPrincipalObjectIds)
    integrationSubnetId: foundation.outputs.integrationSubnetId
    privateEndpointSubnetId: foundation.outputs.privateEndpointSubnetId
    privateDnsZoneIds: foundation.outputs.dnsIds
    existingAmplsResourceId: foundation.outputs.amplsId
    actionGroupResourceId: foundation.outputs.actionGroupId
    premiumSku: 'EP1'
    minimumInstanceCount: 1
    maximumInstanceCount: maximumInstanceCount
    storageSku: 'Standard_LRS'
    logDailyQuotaGb: 1
    processingEnabled: false
    deployRuntimeRoleAssignments: deployRuntimeRoleAssignments
    tags: tags
  }
}
module authoring 'modules/test-authoring.bicep' = {
  name: 'fax-test-authoring'
  scope: authoringGroup
  params: {
    namePrefix: namePrefix
    location: location
    privateEndpointSubnetId: foundation.outputs.privateEndpointSubnetId
    privateDnsZoneIds: foundation.outputs.dnsIds
    authorPrincipalId: foundation.outputs.identities[3].principalId
    tags: tags
  }
}
module runnerAccess 'modules/test-runtime-access.bicep' = {
  name: 'fax-runner-access'
  scope: runtimeGroup
  params: {
    dataStorageName: runtime.outputs.dataStorageAccountName
    hostStorageName: runtime.outputs.hostStorageAccountName
    functionAppName: runtime.outputs.functionAppName
    documentIntelligenceName: last(split(runtime.outputs.documentIntelligenceResourceId, '/'))
    uploaderPrincipalId: foundation.outputs.identities[0].principalId
    deployerPrincipalId: foundation.outputs.identities[2].principalId
    authorPrincipalId: foundation.outputs.identities[3].principalId
  }
}
module monitorEndpoint 'modules/test-monitor-endpoint.bicep' = {
  name: 'fax-monitor-endpoint'
  scope: runtimeGroup
  params: {
    namePrefix: namePrefix
    location: location
    subnetId: foundation.outputs.privateEndpointSubnetId
    amplsId: foundation.outputs.amplsId
    dnsZoneIds: foundation.outputs.monitorDnsIds
    tags: tags
  }
  dependsOn: [runtime]
}
output runnerResourceId string = foundation.outputs.runnerId
output runnerName string = foundation.outputs.runnerName
output uploaderIdentity object = foundation.outputs.identities[0]
output reviewerIdentity object = foundation.outputs.identities[1]
output deployerIdentity object = foundation.outputs.identities[2]
output authorIdentity object = foundation.outputs.identities[3]
output functionAppName string = runtime.outputs.functionAppName
output functionAppResourceId string = runtime.outputs.functionAppResourceId
output apiBaseUrl string = runtime.outputs.apiBaseUrl
output apiClientId string = apiClientId
output humanReviewAuthorization object = {
  clientApplicationIds: humanReviewClientApplicationIds
  principalObjectIds: humanReviewPrincipalObjectIds
  directoryRolesGrantedByTemplate: false
}
output dataStorageAccountName string = runtime.outputs.dataStorageAccountName
output dataStorageAccountUrl string = runtime.outputs.dataStorageAccountUrl
output hostStorageAccountName string = runtime.outputs.hostStorageAccountName
output runtimeDocumentIntelligenceEndpoint string = runtime.outputs.documentIntelligenceEndpoint
output runtimeDocumentIntelligenceResourceId string = runtime.outputs.documentIntelligenceResourceId
output authoringDocumentIntelligenceEndpoint string = authoring.outputs.documentIntelligenceEndpoint
output authoringDocumentIntelligenceResourceId string = authoring.outputs.documentIntelligenceId
output authoringDocumentIntelligencePrincipalId string = authoring.outputs.documentIntelligencePrincipalId
output trainingStorageAccountName string = authoring.outputs.accountName
output trainingStorageAccountUrl string = authoring.outputs.blobUrl
output monitorPrivateEndpointId string = monitorEndpoint.outputs.id
output trainingPrivateEndpointId string = authoring.outputs.privateEndpointId
output allPrivateEndpointIds array = concat(runtime.outputs.privateEndpointIds, [monitorEndpoint.outputs.id, authoring.outputs.privateEndpointId, authoring.outputs.documentIntelligencePrivateEndpointId])
output privateDnsZoneIds object = foundation.outputs.dnsIds
output applicationInsightsResourceId string = runtime.outputs.applicationInsightsResourceId
output logAnalyticsWorkspaceResourceId string = runtime.outputs.logAnalyticsWorkspaceResourceId
output operationsWorkbookResourceId string = runtime.outputs.operationsWorkbookResourceId
output qualityWorkbookResourceId string = runtime.outputs.qualityWorkbookResourceId
