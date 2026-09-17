param dataStorageName string
param hostStorageName string
param functionAppName string
param documentIntelligenceName string
param uploaderPrincipalId string
param deployerPrincipalId string
param authorPrincipalId string
resource data 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: dataStorageName }
resource host 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: hostStorageName }
resource hostBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = { parent: host, name: 'default' }
resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = { parent: hostBlobs, name: 'packages' }
resource packagePublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: packages
  name: guid(packages.id, deployerPrincipalId, 'private-package-publisher')
  properties: {
    principalId: deployerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = { parent: data, name: 'default' }
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [for name in ['incoming', 'ready']: {
  parent: blobs
  name: name
}]
resource uploads 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, 2): {
  scope: containers[i]
  name: guid(containers[i].id, uploaderPrincipalId, 'uploader')
  properties: {
    principalId: uploaderPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]
resource observer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: blobs
  name: guid(blobs.id, authorPrincipalId, 'synthetic-qualification-reader')
  properties: {
    principalId: authorPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  }
}
resource app 'Microsoft.Web/sites@2024-04-01' existing = { name: functionAppName }
resource deployer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: app
  name: guid(app.id, deployerPrincipalId, 'private-scm')
  properties: {
    principalId: deployerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'de139f84-1756-47ae-9be6-808fbbe84772')
  }
}
resource di 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: documentIntelligenceName }
resource modelPromotion 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: di
  name: guid(di.id, authorPrincipalId, 'model-promotion')
  properties: {
    principalId: authorPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}
