param name string
param location string
param tags object
param applicationInsightsResourceId string
param logAnalyticsWorkspaceResourceId string

var operationsData = replace(
  loadTextContent('../workbooks/fax-operations.workbook.json'),
  '{workspaceResourceId}',
  logAnalyticsWorkspaceResourceId
)
var qualityData = replace(
  loadTextContent('../workbooks/fax-quality.workbook.json'),
  '{workspaceResourceId}',
  logAnalyticsWorkspaceResourceId
)

resource operations 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, name, 'fax-operations')
  location: location
  kind: 'shared'
  tags: tags
  properties: {
    displayName: 'Fax Processing - Operations'
    description: 'Operational traffic, failures, workflow outcomes, backlog, and latency.'
    category: 'workbook'
    sourceId: applicationInsightsResourceId
    serializedData: operationsData
    version: 'Notebook/1.0'
  }
}

resource quality 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, name, 'fax-quality')
  location: location
  kind: 'shared'
  tags: tags
  properties: {
    displayName: 'Fax Processing - Quality and Release'
    description: 'Safe model, confidence, rejection, HL7, and release-evidence views.'
    category: 'workbook'
    sourceId: applicationInsightsResourceId
    serializedData: qualityData
    version: 'Notebook/1.0'
  }
}

output operationsWorkbookId string = operations.id
output qualityWorkbookId string = quality.id
