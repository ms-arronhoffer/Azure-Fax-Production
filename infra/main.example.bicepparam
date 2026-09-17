using './main.bicep'

// Illustrative IDs ONLY. Copy to main.prod.bicepparam and replace every example value.
param namePrefix = 'faxprod'
param location = 'eastus2'
param apiClientId = '11111111-1111-1111-1111-111111111111'
param allowedClientApplicationIds = [
  '22222222-2222-2222-2222-222222222222'
]
// Set this only when oid restrictions are required; these are NOT application client IDs.
param allowedPrincipalObjectIds = []
param integrationSubnetId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-private/subnets/functions-integration'
param privateEndpointSubnetId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-private/subnets/private-endpoints'
param privateDnsZoneIds = {
  blob: '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-dns/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net'
  queue: '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-dns/providers/Microsoft.Network/privateDnsZones/privatelink.queue.core.windows.net'
  table: '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-dns/providers/Microsoft.Network/privateDnsZones/privatelink.table.core.windows.net'
  file: '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-dns/providers/Microsoft.Network/privateDnsZones/privatelink.file.core.windows.net'
  sites: '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-dns/providers/Microsoft.Network/privateDnsZones/privatelink.azurewebsites.net'
  cognitiveServices: '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-dns/providers/Microsoft.Network/privateDnsZones/privatelink.cognitiveservices.azure.com'
}
param existingAmplsResourceId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-monitor/providers/Microsoft.Insights/privateLinkScopes/ampls-private'
param actionGroupResourceId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-monitor/providers/Microsoft.Insights/actionGroups/ag-fax-oncall'
param premiumSku = 'EP1'
param minimumInstanceCount = 1
param maximumInstanceCount = 5
param storageSku = 'Standard_ZRS'
param processingEnabled = false
param logRetentionInDays = 30
param logDailyQuotaGb = 5
param http5xxThreshold = 5
param queuedMessageThreshold = 100
