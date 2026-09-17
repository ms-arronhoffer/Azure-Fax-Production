param namePrefix string
param location string
param privateEndpointSubnetId string
param privateDnsZoneIds object
param authorPrincipalId string
param tags object
var suffix = uniqueString(resourceGroup().id, namePrefix)
var diName = 'di-${namePrefix}-author-${suffix}'

module di 'document-intelligence.bicep' = {
  name: 'authoring-di'
  params: {
    name: diName
    location: location
    privateEndpointSubnetId: privateEndpointSubnetId
    privateDnsZoneId: privateDnsZoneIds.cognitiveServices
    enableManagedIdentity: true
    tags: tags
  }
}
// Deliberately separate exception from the reusable production storage module.
resource training 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stfaxtrain${suffix}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      ipRules: []
      virtualNetworkRules: []
      resourceAccessRules: [{ resourceId: di.outputs.id, tenantId: subscription().tenantId }]
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      requireInfrastructureEncryption: true
      services: { blob: { enabled: true, keyType: 'Account' } }
    }
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: training
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}
resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'training'
  properties: { publicAccess: 'None' }
}
resource trainingAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, 2): {
  scope: container
  name: guid(container.id, string(i), 'training-contributor')
  properties: {
    principalId: i == 0 ? authorPrincipalId : di.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]
resource authoringAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: diName
}
resource authorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authoringAccount.id, authorPrincipalId, 'author')
  scope: authoringAccount
  properties: {
    principalId: authorPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
  dependsOn: [di]
}
module endpoint 'private-endpoint.bicep' = {
  name: 'training-blob-endpoint'
  params: {
    name: 'pe-${training.name}-blob'
    location: location
    subnetId: privateEndpointSubnetId
    targetResourceId: training.id
    groupId: 'blob'
    privateDnsZoneId: privateDnsZoneIds.blob
    tags: tags
  }
}
output accountName string = training.name
output blobUrl string = training.properties.primaryEndpoints.blob
output documentIntelligenceEndpoint string = di.outputs.endpoint
output documentIntelligenceId string = di.outputs.id
output documentIntelligencePrincipalId string = di.outputs.principalId
output privateEndpointId string = endpoint.outputs.id
output documentIntelligencePrivateEndpointId string = di.outputs.privateEndpointId
