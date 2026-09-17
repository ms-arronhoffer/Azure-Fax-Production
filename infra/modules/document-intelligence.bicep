param name string
param location string
param privateEndpointSubnetId string
param privateDnsZoneId string
param tags object
param enableManagedIdentity bool = false

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'FormRecognizer'
  identity: {
    type: enableManagedIdentity ? 'SystemAssigned' : 'None'
  }
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

module endpoint 'private-endpoint.bicep' = {
  name: '${name}-endpoint'
  params: {
    name: 'pe-${name}'
    location: location
    subnetId: privateEndpointSubnetId
    targetResourceId: account.id
    groupId: 'account'
    privateDnsZoneId: privateDnsZoneId
    tags: tags
  }
}

output name string = account.name
output id string = account.id
output endpoint string = account.properties.endpoint
output principalId string = enableManagedIdentity ? account.identity.principalId : ''
output privateEndpointId string = endpoint.outputs.id
