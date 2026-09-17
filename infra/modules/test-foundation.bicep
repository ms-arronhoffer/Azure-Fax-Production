param namePrefix string
param location string
param runnerSshPublicKey string
@allowed(['Standard_LRS', 'StandardSSD_LRS'])
param runnerOsDiskStorageAccountType string = 'StandardSSD_LRS'
param alertEmail string
param tags object

module nat 'br/public:avm/res/network/nat-gateway:2.1.1' = {
  name: 'test-nat'
  params: {
    name: 'nat-${namePrefix}'
    location: location
    availabilityZone: -1
    publicIPAddresses: [{ name: 'pip-${namePrefix}-egress' }]
    tags: tags
    enableTelemetry: false
  }
}

module runnerNsg 'br/public:avm/res/network/network-security-group:0.5.3' = {
  name: 'runner-nsg'
  params: {
    name: 'nsg-${namePrefix}-runner'
    location: location
    tags: tags
    enableTelemetry: false
    securityRules: [
      {
        name: 'DenyAllInbound'
        properties: {
          access: 'Deny'
          direction: 'Inbound'
          priority: 100
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

module vnet 'br/public:avm/res/network/virtual-network:0.10.2' = {
  name: 'test-vnet'
  params: {
    name: 'vnet-${namePrefix}'
    location: location
    addressPrefixes: ['10.84.0.0/16']
    tags: tags
    enableTelemetry: false
    subnets: [
      {
        name: 'integration'
        addressPrefix: '10.84.0.0/26'
        delegation: 'Microsoft.Web/serverFarms'
        natGatewayResourceId: nat.outputs.resourceId
        defaultOutboundAccess: false
      }
      {
        name: 'private-endpoints'
        addressPrefix: '10.84.1.0/24'
        privateEndpointNetworkPolicies: 'Disabled'
        defaultOutboundAccess: false
      }
      {
        name: 'runner'
        addressPrefix: '10.84.2.0/27'
        natGatewayResourceId: nat.outputs.resourceId
        networkSecurityGroupResourceId: runnerNsg.outputs.resourceId
        defaultOutboundAccess: false
      }
    ]
  }
}

var zoneNames = [
  'privatelink.blob.core.windows.net'
  'privatelink.queue.core.windows.net'
  'privatelink.table.core.windows.net'
  'privatelink.file.core.windows.net'
  'privatelink.azurewebsites.net'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.monitor.azure.com'
  'privatelink.oms.opinsights.azure.com'
  'privatelink.ods.opinsights.azure.com'
  'privatelink.agentsvc.azure-automation.net'
]
module dns 'br/public:avm/res/network/private-dns-zone:0.8.1' = [for (zone, i) in zoneNames: {
  name: 'private-dns-${i}'
  params: {
    name: zone
    tags: tags
    enableTelemetry: false
    virtualNetworkLinks: [
      {
        name: 'fax-test-vnet'
        virtualNetworkResourceId: vnet.outputs.resourceId
        registrationEnabled: false
      }
    ]
  }
}]

module identities 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = [for purpose in ['uploader', 'reviewer', 'deployer', 'author']: {
  name: 'identity-${purpose}'
  params: {
    name: 'id-${namePrefix}-${purpose}'
    location: location
    tags: tags
    enableTelemetry: false
  }
}]

module runner 'br/public:avm/res/compute/virtual-machine:0.22.3' = {
  name: 'test-runner'
  params: {
    name: 'vm-${namePrefix}-runner'
    location: location
    vmSize: 'Standard_D2s_v7'
    availabilityZone: -1
    osType: 'Linux'
    adminUsername: 'faxrunner'
    disablePasswordAuthentication: true
    publicKeys: [{ path: '/home/faxrunner/.ssh/authorized_keys', keyData: runnerSshPublicKey }]
    imageReference: {
      publisher: 'Debian'
      offer: 'debian-12'
      sku: '12-gen2'
      version: 'latest'
    }
    osDisk: {
      createOption: 'FromImage'
      diskSizeGB: 64
      managedDisk: { storageAccountType: runnerOsDiskStorageAccountType }
    }
    securityType: 'TrustedLaunch'
    secureBootEnabled: true
    vTpmEnabled: true
    bootDiagnostics: false
    managedIdentities: {
      userAssignedResourceIds: [for i in range(0, 4): identities[i].outputs.resourceId]
    }
    nicConfigurations: [
      {
        nicSuffix: '-nic'
        ipConfigurations: [
          {
            name: 'private'
            subnetResourceId: '${vnet.outputs.resourceId}/subnets/runner'
          }
        ]
      }
    ]
    tags: tags
    enableTelemetry: false
  }
}

module ampls 'br/public:avm/res/insights/private-link-scope:0.7.3' = {
  name: 'test-ampls'
  params: {
    name: 'ampls-${namePrefix}'
    accessModeSettings: { ingestionAccessMode: 'PrivateOnly', queryAccessMode: 'PrivateOnly' }
    tags: tags
    enableTelemetry: false
  }
}
module actionGroup 'br/public:avm/res/insights/action-group:0.8.0' = {
  name: 'test-action-group'
  params: {
    name: 'ag-${namePrefix}'
    groupShortName: 'fax-test'
    emailReceivers: [{ name: 'test-owner', emailAddress: alertEmail, useCommonAlertSchema: true }]
    tags: tags
    enableTelemetry: false
  }
}

output vnetId string = vnet.outputs.resourceId
output integrationSubnetId string = '${vnet.outputs.resourceId}/subnets/integration'
output privateEndpointSubnetId string = '${vnet.outputs.resourceId}/subnets/private-endpoints'
output dnsIds object = {
  blob: dns[0].outputs.resourceId
  queue: dns[1].outputs.resourceId
  table: dns[2].outputs.resourceId
  file: dns[3].outputs.resourceId
  sites: dns[4].outputs.resourceId
  cognitiveServices: dns[5].outputs.resourceId
}
output monitorDnsIds array = [dns[6].outputs.resourceId, dns[7].outputs.resourceId, dns[8].outputs.resourceId, dns[9].outputs.resourceId, dns[0].outputs.resourceId]
output amplsId string = ampls.outputs.resourceId
output actionGroupId string = actionGroup.outputs.resourceId
output runnerId string = runner.outputs.resourceId
output runnerName string = runner.outputs.name
output identities array = [for i in range(0, 4): {
  resourceId: identities[i].outputs.resourceId
  clientId: identities[i].outputs.clientId
  principalId: identities[i].outputs.principalId
}]
