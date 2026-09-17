param name string
param location string
param functionAppId string
param queueServiceId string
@minLength(1)
param actionGroupResourceId string
param tags object
@minValue(1)
param http5xxThreshold int
@minValue(1)
param queuedMessageThreshold int

resource failures 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${name}-http-failures'
  location: 'global'
  tags: tags
  properties: {
    description: 'Private fax API HTTP 5xx responses. Investigate without logging fax contents.'
    severity: 1
    enabled: true
    scopes: [functionAppId]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Web/sites'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'serverErrors'
          metricName: 'Http5xx'
          metricNamespace: 'Microsoft.Web/sites'
          operator: 'GreaterThanOrEqual'
          threshold: http5xxThreshold
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroupResourceId }]
  }
}

resource backlog 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${name}-queue-backlog'
  location: 'global'
  tags: tags
  properties: {
    description: 'Hourly queue capacity count across fax-jobs and poison queue. Not a per-queue or real-time SLA.'
    severity: 2
    enabled: true
    scopes: [queueServiceId]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT1H'
    targetResourceType: 'Microsoft.Storage/storageAccounts/queueServices'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'queueCount'
          metricName: 'QueueMessageCount'
          metricNamespace: 'Microsoft.Storage/storageAccounts/queueServices'
          operator: 'GreaterThanOrEqual'
          threshold: queuedMessageThreshold
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroupResourceId }]
  }
}
