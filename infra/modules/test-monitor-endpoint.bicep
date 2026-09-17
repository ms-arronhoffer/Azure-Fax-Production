param namePrefix string
param location string
param subnetId string
param amplsId string
param dnsZoneIds array
param tags object
module endpoint 'br/public:avm/res/network/private-endpoint:0.10.1' = {
  name: 'monitor-private-endpoint'
  params: {
    name: 'pe-${namePrefix}-monitor'
    location: location
    subnetResourceId: subnetId
    privateLinkServiceConnections: [{
      name: 'azuremonitor'
      properties: { privateLinkServiceId: amplsId, groupIds: ['azuremonitor'] }
    }]
    privateDnsZoneGroup: {
      privateDnsZoneGroupConfigs: [for id in dnsZoneIds: {
        name: last(split(id, '/'))
        privateDnsZoneResourceId: id
      }]
    }
    tags: tags
    enableTelemetry: false
  }
}
output id string = endpoint.outputs.resourceId
