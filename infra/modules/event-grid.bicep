param name string
param storageAccountId string
param functionAppId string
param deadLetterContainerName string = 'event-grid-deadletter'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(storageAccountId, '/'))
}

// A source-scoped subscription reuses an existing tracked system topic (for
// example, one created by Defender for Storage) instead of creating a competing
// second system topic, which Azure rejects.
resource readySubscription 'Microsoft.EventGrid/eventSubscriptions@2023-12-15-preview' = {
  name: name
  scope: storage
  properties: {
    deadLetterDestination: {
      endpointType: 'StorageBlob'
      properties: {
        resourceId: storage.id
        blobContainerName: deadLetterContainerName
      }
    }
    destination: {
      endpointType: 'AzureFunction'
      properties: {
        resourceId: '${functionAppId}/functions/ready_manifest'
        maxEventsPerBatch: 1
        preferredBatchSizeInKilobytes: 64
      }
    }
    eventDeliverySchema: 'EventGridSchema'
    filter: {
      includedEventTypes: [
        'Microsoft.Storage.BlobCreated'
      ]
      isSubjectCaseSensitive: true
      subjectBeginsWith: '/blobServices/default/containers/ready/blobs/'
      advancedFilters: [
        {
          key: 'data.api'
          operatorType: 'StringIn'
          values: [
            'PutBlob'
            'PutBlockList'
            'FlushWithClose'
          ]
        }
      ]
    }
    retryPolicy: {
      eventTimeToLiveInMinutes: 1440
      maxDeliveryAttempts: 30
    }
  }
}

output id string = readySubscription.id
output name string = readySubscription.name
