param principalId string
param hostStorageName string
param dataStorageName string
param documentIntelligenceName string
param insightsName string
param deployAssignments bool = true

var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var queueContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var hostRoles = [
  'b7e6dc6d-f1e8-4753-8033-0f276bb0955b' // Host: Storage Blob Data Owner (includes Durable blob contributor).
  queueContributor
  '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3' // Durable and host diagnostics tables.
]
var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var containerNames = ['incoming', 'working', 'extracted', 'routed', 'human-review', 'jobs']
var queueNames = ['fax-jobs', 'fax-jobs-poison']

resource host 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: hostStorageName
}
resource data 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: dataStorageName
}
resource di 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: documentIntelligenceName
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: insightsName
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: data
  name: 'default'
}
resource queues 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' existing = {
  parent: data
  name: 'default'
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = [for name in containerNames: {
  parent: blobs
  name: name
}]
resource dataQueues 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' existing = [for name in queueNames: {
  parent: queues
  name: name
}]

resource ready 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobs
  name: 'ready'
}
resource readyAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAssignments) {
  name: guid(ready.id, principalId, 'blob-trigger-owner')
  scope: ready
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
resource triggerQueueAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAssignments) {
  name: guid(queues.id, principalId, queueContributor)
  scope: queues
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

var inferenceRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)

resource hostAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in hostRoles: if (deployAssignments) {
  name: guid(host.id, principalId, role)
  scope: host
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]

resource dataBlobAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (containerName, i) in containerNames: if (deployAssignments) {
  name: guid(containers[i].id, principalId, blobContributor)
  scope: containers[i]
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]

resource dataQueueAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (queueName, i) in queueNames: if (deployAssignments) {
  name: guid(dataQueues[i].id, principalId, queueContributor)
  scope: dataQueues[i]
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]

resource cognitiveAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAssignments) {
  name: guid(di.id, principalId, cognitiveServicesUser)
  scope: di
  properties: {
    roleDefinitionId: inferenceRoleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output documentIntelligenceRuntimeRoleDefinitionId string = inferenceRoleDefinitionId

resource metricsAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAssignments) {
  name: guid(insights.id, principalId, metricsPublisher)
  scope: insights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
