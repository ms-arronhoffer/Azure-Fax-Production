param name string
param location string
param isHost bool
param privateEndpointSubnetId string
param privateDnsZoneIds object
param tags object
@allowed(['Standard_LRS', 'Standard_ZRS'])
param skuName string
param contentShareName string = 'function-content'

var containers = isHost ? ['packages'] : [
  'incoming'
  'ready'
  'working'
  'extracted'
  'routed'
  'human-review'
  'jobs'
  'event-grid-deadletter'
]
var queues = isHost ? [] : ['fax-jobs', 'fax-jobs-poison', 'webjobs-blobtrigger-poison']
var services = isHost ? ['blob', 'queue', 'table', 'file'] : ['blob', 'queue']

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: skuName
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      // Storage event publication requires the trusted Azure-services bypass.
      // Public data-plane access remains disabled and the default action is deny.
      bypass: isHost ? 'None' : 'AzureServices'
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      requireInfrastructureEncryption: true
      services: {
        blob: { enabled: true, keyType: 'Account' }
        file: { enabled: true, keyType: 'Account' }
        queue: { enabled: true, keyType: 'Account' }
        table: { enabled: true, keyType: 'Account' }
      }
    }
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {
    isVersioningEnabled: !isHost
    deleteRetentionPolicy: {
      enabled: !isHost
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: !isHost
      days: 7
    }
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for containerName in containers: {
  parent: blobs
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}]

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {}
}

resource queue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = [for queueName in queues: {
  parent: queueService
  name: queueName
  properties: {}
}]

resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = if (isHost) {
  parent: account
  name: 'default'
  properties: {
    protocolSettings: {
      smb: {
        versions: 'SMB3.1.1;SMB3.0'
      }
    }
  }
}

resource contentShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = if (isHost) {
  parent: files
  name: contentShareName
  properties: {
    shareQuota: 100
    enabledProtocols: 'SMB'
  }
}

module endpoints 'private-endpoint.bicep' = [for service in services: {
  name: '${name}-${service}-endpoint'
  params: {
    name: 'pe-${name}-${service}'
    location: location
    subnetId: privateEndpointSubnetId
    targetResourceId: account.id
    groupId: service
    privateDnsZoneId: privateDnsZoneIds[service]
    tags: tags
  }
}]

output accountName string = account.name
output id string = account.id
output blobUrl string = account.properties.primaryEndpoints.blob
output queueUrl string = account.properties.primaryEndpoints.queue
output tableUrl string = account.properties.primaryEndpoints.table
output queueServiceId string = queueService.id
output shareName string = contentShareName
output privateEndpointIds array = [for i in range(0, length(services)): endpoints[i].outputs.id]
