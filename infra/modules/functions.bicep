param name string
param planName string = 'plan-${name}'
param location string
param tags object
param integrationSubnetId string
param privateEndpointSubnetId string
param privateDnsZoneId string
param hostBlobUrl string
param deploymentPackageUrl string = ''
param hostQueueUrl string
param hostTableUrl string
param dataBlobUrl string
param dataQueueUrl string
param documentIntelligenceEndpoint string
param applicationInsightsConnectionString string
param runtimeIdentityResourceId string
param runtimeIdentityClientId string
param tenantId string
param apiClientId string
@minLength(1)
param allowedClientApplicationIds array
@maxLength(13)
param allowedPrincipalObjectIds array
param processingEnabled bool
@allowed(['EP1', 'EP2', 'EP3'])
param premiumSku string
@minValue(1)
@maxValue(20)
param maximumInstanceCount int
@minValue(1)
@maxValue(20)
param minimumInstanceCount int

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'elastic'
  sku: {
    name: premiumSku
    tier: 'ElasticPremium'
    capacity: minimumInstanceCount
  }
  properties: {
    reserved: true
    maximumElasticWorkerCount: maximumInstanceCount
  }
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: name
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${runtimeIdentityResourceId}': {}
    }
  }
  properties: {
    serverFarmId: plan.id
    reserved: true
    httpsOnly: true
    // Event Grid invokes the host webhook over the public App Service ingress.
    // Default-deny restrictions expose only the AzureEventGrid service tag;
    // application and SCM traffic continue to use the private endpoint.
    publicNetworkAccess: 'Enabled'
    virtualNetworkSubnetId: integrationSubnetId
    vnetRouteAllEnabled: true
    vnetContentShareEnabled: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      http20Enabled: true
      remoteDebuggingEnabled: false
      alwaysOn: true
      functionsRuntimeScaleMonitoringEnabled: true
      functionAppScaleLimit: maximumInstanceCount
      minimumElasticInstanceCount: minimumInstanceCount
      ipSecurityRestrictionsDefaultAction: 'Deny'
      ipSecurityRestrictions: [
        {
          name: 'Allow-Azure-Event-Grid'
          description: 'Allow direct Event Grid delivery to the host webhook'
          action: 'Allow'
          priority: 100
          ipAddress: 'AzureEventGrid'
          tag: 'ServiceTag'
        }
      ]
      scmIpSecurityRestrictionsDefaultAction: 'Deny'
      scmIpSecurityRestrictionsUseMain: false
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsFeatureFlags', value: 'EnableWorkerIndexing' }
        { name: 'WEBSITE_CONTENTOVERVNET', value: '1' }
        { name: 'WEBSITE_VNET_ROUTE_ALL', value: '1' }
        { name: 'AZURE_FUNCTIONS_ENVIRONMENT', value: 'Production' }
        { name: 'AZURE_CLIENT_ID', value: runtimeIdentityClientId }
        { name: 'AzureWebJobsStorage__blobServiceUri', value: hostBlobUrl }
        { name: 'AzureWebJobsStorage__queueServiceUri', value: hostQueueUrl }
        { name: 'AzureWebJobsStorage__tableServiceUri', value: hostTableUrl }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: runtimeIdentityClientId }
        { name: 'FaxQueueConnection__queueServiceUri', value: dataQueueUrl }
        { name: 'FaxQueueConnection__credential', value: 'managedidentity' }
        { name: 'FaxQueueConnection__clientId', value: runtimeIdentityClientId }
        { name: 'FaxBlobConnection__blobServiceUri', value: dataBlobUrl }
        { name: 'FaxBlobConnection__queueServiceUri', value: dataQueueUrl }
        { name: 'FaxBlobConnection__credential', value: 'managedidentity' }
        { name: 'FaxBlobConnection__clientId', value: runtimeIdentityClientId }
        { name: 'DATA_STORAGE_ACCOUNT_URL', value: dataBlobUrl }
        { name: 'DATA_QUEUE_URL', value: dataQueueUrl }
        { name: 'DOCUMENT_INTELLIGENCE_ENDPOINT', value: documentIntelligenceEndpoint }
        { name: 'PROCESSING_ENABLED', value: toLower(string(processingEnabled)) }
        { name: 'MAX_UPLOAD_BYTES', value: '20971520' }
        { name: 'MAX_PAGES', value: '100' }
        { name: 'ENTRA_TENANT_ID', value: tenantId }
        { name: 'ENTRA_API_AUDIENCE', value: apiClientId }
        { name: 'WEBSITE_AUTH_AAD_ALLOWED_TENANTS', value: tenantId }
        { name: 'WEBSITE_AUTH_AAD_REQUIRE_CLIENT_SERVICE_PRINCIPAL', value: 'true' }
        { name: 'WEBSITE_RUN_FROM_PACKAGE', value: empty(deploymentPackageUrl) ? '${hostBlobUrl}packages/bootstrap.zip' : deploymentPackageUrl }
        { name: 'WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID', value: 'SystemAssigned' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'false' }
        { name: 'ENABLE_ORYX_BUILD', value: 'false' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: applicationInsightsConnectionString }
        { name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING', value: 'Authorization=AAD' }
        { name: 'AzureWebJobsDisableHomepage', value: 'true' }
      ]
    }
  }
}

resource auth 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: app
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
      // The Event Grid extension authenticates this host webhook with its
      // function-system key. Public reachability is restricted above to the
      // AzureEventGrid service tag.
      excludedPaths: [
        '/runtime/webhooks/EventGrid'
        '/runtime/webhooks/eventgrid'
      ]
    }
    httpSettings: {
      requireHttps: true
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: apiClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [apiClientId, 'api://${apiClientId}']
          defaultAuthorizationPolicy: union({
            allowedApplications: allowedClientApplicationIds
          }, empty(allowedPrincipalObjectIds) ? {} : {
            allowedPrincipals: {
              identities: allowedPrincipalObjectIds
            }
          })
        }
      }
    }
    login: {
      tokenStore: {
        enabled: false
      }
    }
  }
}

resource basicScm 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: app
  name: 'scm'
  properties: { allow: false }
}

resource basicFtp 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: app
  name: 'ftp'
  properties: { allow: false }
}

module endpoint 'private-endpoint.bicep' = {
  name: '${name}-endpoint'
  params: {
    name: 'pe-${name}'
    location: location
    subnetId: privateEndpointSubnetId
    targetResourceId: app.id
    groupId: 'sites'
    privateDnsZoneId: privateDnsZoneId
    tags: tags
  }
  dependsOn: [auth, basicScm, basicFtp]
}

output name string = app.name
output id string = app.id
output principalId string = app.identity.principalId
output apiBaseUrl string = 'https://${app.properties.defaultHostName}/api'
output scmHostName string = '${app.name}.scm.azurewebsites.net'
output privateEndpointId string = endpoint.outputs.id
