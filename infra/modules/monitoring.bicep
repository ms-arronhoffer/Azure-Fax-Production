param name string
param location string
param tags object
@minValue(30)
@maxValue(730)
param retentionInDays int
@minValue(1)
param dailyQuotaGb int

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${name}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: retentionInDays
    workspaceCapping: { dailyQuotaGb: dailyQuotaGb }
    publicNetworkAccessForIngestion: 'Disabled'
    publicNetworkAccessForQuery: 'Disabled'
    features: {
      disableLocalAuth: true
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${name}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    DisableLocalAuth: true
    publicNetworkAccessForIngestion: 'Disabled'
    publicNetworkAccessForQuery: 'Disabled'
  }
}

output workspaceId string = workspace.id
output insightsId string = insights.id
output insightsName string = insights.name
output connectionString string = insights.properties.ConnectionString
