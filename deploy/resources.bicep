param location string
param resourcePrefix string
param foundryProjectName string
param modelDeploymentName string
param modelName string
param modelVersion string
param modelSkuName string
param modelCapacity int
param deployerPrincipalId string
param deployerClientIp string = ''
param functionAppName string = ''
param functionPlanSku string = 'FC1'
param logAnalyticsName string = ''
param appInsightsName string = ''
param existingFoundryAccountName string = ''
param existingLogAnalyticsName string = ''
param existingAppInsightsName string = ''
param mcpAllowedAudiences string = ''
param fabricCapacityName string = ''
param fabricCapacitySku string = 'F4'
param fabricCapacityAdmin string = ''
param allowedTenantId string = subscription().tenantId
param policySha256 string = ''
param tags object

resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = if (!empty(fabricCapacityName) && !empty(fabricCapacityAdmin)) {
  name: fabricCapacityName
  location: location
  tags: tags
  sku: {
    name: fabricCapacitySku
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: [
        fabricCapacityAdmin
      ]
    }
  }
}

var suffix = uniqueString(resourceGroup().id)
var useExistingFoundry = !empty(existingFoundryAccountName)
var useExistingLaw = !empty(existingLogAnalyticsName)
var useExistingAppi = !empty(existingAppInsightsName)
var defaultAllowedAudiences = '${environment().resourceManager},https://ai.azure.com/,https://ai.azure.com,https://cognitiveservices.azure.com/,https://cognitiveservices.azure.com'
var resolvedAllowedAudiences = !empty(mcpAllowedAudiences) ? mcpAllowedAudiences : defaultAllowedAudiences
var storageName = toLower('${take(resourcePrefix, 10)}rcpt${take(suffix, 8)}')
var hostStorageName = toLower('${take(resourcePrefix, 10)}host${take(suffix, 8)}')
var foundryName = useExistingFoundry ? existingFoundryAccountName : toLower('${resourcePrefix}-ai-${take(suffix, 6)}')
var resolvedFuncName = !empty(functionAppName) ? functionAppName : toLower('${resourcePrefix}-mcp-${take(suffix, 6)}')
var resolvedLawName = useExistingLaw ? existingLogAnalyticsName : (!empty(logAnalyticsName) ? logAnalyticsName : '${resourcePrefix}-law')
var resolvedAppiName = useExistingAppi ? existingAppInsightsName : (!empty(appInsightsName) ? appInsightsName : '${resourcePrefix}-appi')
var isFlexConsumption = toUpper(functionPlanSku) == 'FC1'
var isDynamicPlan = toUpper(functionPlanSku) == 'Y1'

var blobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var blobDataOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var blobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var queueDataContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var tableDataContributor = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var openAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var aiUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${resourcePrefix}-agent-id'
  location: location
  tags: tags
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'SecuredByPerimeter'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: false
    deleteRetentionPolicy: { enabled: false }
    containerDeleteRetentionPolicy: { enabled: false }
  }
}

resource receipts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'receipts'
  properties: {
    publicAccess: 'None'
  }
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = if (!useExistingFoundry) {
  name: foundryName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: foundryName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource existingFoundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = if (useExistingFoundry) {
  name: existingFoundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (!useExistingFoundry) {
  parent: foundry
  name: foundryProjectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {}
  dependsOn: [
    model
  ]
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (!useExistingFoundry) {
  parent: foundry
  name: modelDeploymentName
  sku: { name: modelSkuName, capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: modelName, version: modelVersion }
  }
}

// Read-only for the agent: read receipts, invoke the model. No write roles.
resource agentBlobRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, identity.id, blobDataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataReader)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource agentModelUse 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundry) {
  scope: foundry
  name: guid(foundry.id, identity.id, openAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource projectModelUse 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundry) {
  scope: foundry
  name: guid(foundry.id, project.id, openAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
    principalId: project!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerBlobWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  scope: storage
  name: guid(storage.id, deployerPrincipalId, blobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataContributor)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource deployerAiUse 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundry && !empty(deployerPrincipalId)) {
  scope: foundry
  name: guid(foundry.id, deployerPrincipalId, aiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiUser)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource deployerProjectAiUse 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundry && !empty(deployerPrincipalId)) {
  scope: project
  name: guid(project.id, deployerPrincipalId, aiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiUser)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource deployerOpenAiUse 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundry && !empty(deployerPrincipalId)) {
  scope: foundry
  name: guid(foundry.id, deployerPrincipalId, openAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUser)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource deployerAiUseExisting 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useExistingFoundry && !empty(deployerPrincipalId)) {
  scope: existingFoundry
  name: guid(existingFoundry.id, deployerPrincipalId, aiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiUser)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

// Separate host storage for Azure Functions runtime state so the agent identity stays strictly
// read-only (Storage Blob Data Reader) on the receipts storage account.
resource hostStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: hostStorageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'SecuredByPerimeter'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource nsp 'Microsoft.Network/networkSecurityPerimeters@2023-08-01-preview' = {
  name: '${resourcePrefix}-nsp'
  location: location
  tags: tags
}

resource nspProfile 'Microsoft.Network/networkSecurityPerimeters/profiles@2023-08-01-preview' = {
  parent: nsp
  name: 'default'
  location: location
}

resource nspInboundSubscription 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2023-08-01-preview' = {
  parent: nspProfile
  name: 'allow-subscription-identity'
  location: location
  properties: {
    direction: 'Inbound'
    subscriptions: [
      {
        id: subscription().id
      }
    ]
  }
}

var deployerIpCidr = !empty(deployerClientIp) ? (contains(deployerClientIp, '/') ? deployerClientIp : '${deployerClientIp}/32') : ''

resource nspInboundDeployerIp 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2023-08-01-preview' = if (!empty(deployerIpCidr)) {
  parent: nspProfile
  name: 'allow-deployer-ip'
  location: location
  properties: {
    direction: 'Inbound'
    addressPrefixes: [
      deployerIpCidr
    ]
  }
}

resource nspStorageAssociation 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2023-08-01-preview' = {
  parent: nsp
  name: 'assoc-receipts-storage'
  location: location
  properties: {
    accessMode: 'Learning'
    privateLinkResource: {
      id: storage.id
    }
    profile: {
      id: nspProfile.id
    }
  }
}

resource nspHostStorageAssociation 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2023-08-01-preview' = {
  parent: nsp
  name: 'assoc-host-storage'
  location: location
  properties: {
    accessMode: 'Learning'
    privateLinkResource: {
      id: hostStorage.id
    }
    profile: {
      id: nspProfile.id
    }
  }
}

resource hostBlob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: hostStorage
  name: 'default'
  properties: {
    isVersioningEnabled: false
    deleteRetentionPolicy: { enabled: false }
    containerDeleteRetentionPolicy: { enabled: false }
  }
}

resource deploymentPackageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: hostBlob
  name: 'deploymentpackage'
  properties: {
    publicAccess: 'None'
  }
}

resource hostBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: hostStorage
  name: guid(hostStorage.id, identity.id, blobDataOwner)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataOwner)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource hostQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: hostStorage
  name: guid(hostStorage.id, identity.id, queueDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueDataContributor)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource hostTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: hostStorage
  name: guid(hostStorage.id, identity.id, tableDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', tableDataContributor)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (!useExistingLaw && !useExistingAppi) {
  name: resolvedLawName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource existingLogAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = if (useExistingLaw) {
  name: existingLogAnalyticsName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = if (!useExistingAppi) {
  name: resolvedAppiName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: useExistingLaw ? existingLogAnalytics!.id : logAnalytics!.id
  }
}

resource existingAppInsights 'Microsoft.Insights/components@2020-02-02' existing = if (useExistingAppi) {
  name: existingAppInsightsName
}

resource agentMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingAppi) {
  scope: appInsights
  name: guid(appInsights.id, identity.id, metricsPublisher)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource agentMetricsPublisherExisting 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useExistingAppi) {
  scope: existingAppInsights
  name: guid(existingAppInsights.id, identity.id, metricsPublisher)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${resourcePrefix}-mcp-plan'
  location: location
  tags: tags
  kind: isFlexConsumption ? 'functionapp' : 'functionapp,linux'
  sku: {
    name: functionPlanSku
    tier: isFlexConsumption ? 'FlexConsumption' : (isDynamicPlan ? 'Dynamic' : 'Basic')
  }
  properties: {
    reserved: true
  }
}

var baseAppSettings = [
  { name: 'AzureWebJobsStorage__accountName', value: hostStorage.name }
  { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
  { name: 'AzureWebJobsStorage__clientId', value: identity.properties.clientId }
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
  { name: 'RECEIPTS_STORAGE_ACCOUNT', value: storage.name }
  { name: 'RECEIPTS_CONTAINER', value: receipts.name }
  { name: 'PINNED_POLICY_SHA256', value: policySha256 }
  { name: 'MCP_ALLOWED_TENANT_ID', value: allowedTenantId }
  { name: 'MCP_ALLOWED_AUDIENCES', value: resolvedAllowedAudiences }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: useExistingAppi ? existingAppInsights!.properties.ConnectionString : appInsights!.properties.ConnectionString }
  { name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING', value: 'Authorization=AAD;ClientId=${identity.properties.clientId}' }
]

var legacyRuntimeSettings = [
  { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
  { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
]

// Cloud MCP tool host Function App: wraps FoundryToolbox (4 read-only receipt tools) over streamable HTTP.
// Uses identity-based host storage and reads receipts from `storage` via Storage Blob Data Reader.
resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: resolvedFuncName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    clientAffinityEnabled: false
    functionAppConfig: isFlexConsumption ? {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${hostStorage.properties.primaryEndpoints.blob}${deploymentPackageContainer.name}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: identity.id
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    } : null
    siteConfig: {
      linuxFxVersion: isFlexConsumption ? '' : 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: isFlexConsumption ? baseAppSettings : concat(legacyRuntimeSettings, baseAppSettings)
    }
  }
}

output storageAccountName string = storage.name
output receiptsContainer string = receipts.name
output foundryAccountName string = foundryName
output foundryEndpoint string = useExistingFoundry ? existingFoundry!.properties.endpoint : foundry!.properties.endpoint
output foundryProjectName string = foundryProjectName
output modelDeploymentName string = modelDeploymentName
output agentIdentityName string = identity.name
output agentIdentityClientId string = identity.properties.clientId
output agentIdentityPrincipalId string = identity.properties.principalId
output functionAppName string = functionApp.name
output functionAppEndpoint string = 'https://${functionApp.properties.defaultHostName}/api/mcp'
output appInsightsName string = resolvedAppiName
output logAnalyticsName string = resolvedLawName
