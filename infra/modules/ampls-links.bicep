param amplsName string
param workspaceId string
param insightsId string

resource ampls 'Microsoft.Insights/privateLinkScopes@2021-07-01-preview' existing = {
  name: amplsName
}

resource workspaceLink 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'fax-log-${uniqueString(workspaceId)}'
  properties: {
    linkedResourceId: workspaceId
  }
}

resource insightsLink 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'fax-appi-${uniqueString(insightsId)}'
  properties: {
    linkedResourceId: insightsId
  }
}
