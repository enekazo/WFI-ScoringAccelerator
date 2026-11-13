targetScope = 'subscription'

@description('Azure region.')
param location string
@description('Short prefix (3-10 lowercase alphanumerics) used to name every resource.')
@minLength(3)
@maxLength(10)
param resourcePrefix string
@description('Optional explicit Resource Group name. Defaults to <resourcePrefix>-rg when empty.')
param resourceGroupName string = ''
@description('Foundry project name.')
param foundryProjectName string
@description('Model deployment name.')
param modelDeploymentName string
@description('Model name, e.g. gpt-4o.')
param modelName string = 'gpt-4o'
@description('Model version.')
param modelVersion string = '2024-11-20'
@description('Deployment SKU.')
param modelSkuName string = 'GlobalStandard'
@description('Deployment capacity (thousands of tokens per minute).')
param modelCapacity int = 10
@description('Object id of the deploying principal; granted blob write for the smoke test. Empty to skip.')
param deployerPrincipalId string = ''
@description('Optional client IP or CIDR of the deploying machine to allow through the Network Security Perimeter during receipt upload.')
param deployerClientIp string = ''
@description('Optional Fabric workspace id (recorded as a tag only; the agent identity gets no Fabric rights here).')
param fabricWorkspaceId string = ''
@description('Optional Fabric lakehouse id (recorded as a tag only).')
param fabricLakehouseId string = ''
@description('Pinned policy SHA-256, recorded as a tag for traceability.')
param policySha256 string = ''
@description('Optional Function App name override for the MCP receipt tool host.')
param functionAppName string = ''
@description('App Service plan SKU for the MCP receipt tool host (default FC1 Flex Consumption; or Y1/B1).')
param functionPlanSku string = 'FC1'
@description('Optional Log Analytics workspace name override.')
param logAnalyticsName string = ''
@description('Optional Application Insights component name override.')
param appInsightsName string = ''
@description('Optional existing Azure AI Foundry (AIServices) account name in the target resource group. When set, reuses the existing account, project, and model deployment.')
param existingFoundryAccountName string = ''
@description('Optional existing Log Analytics workspace name in the target resource group. When set, reuses the existing workspace instead of creating a new one.')
param existingLogAnalyticsName string = ''
@description('Optional existing Application Insights component name in the target resource group. When set, reuses the existing component instead of creating a new one.')
param existingAppInsightsName string = ''
@description('Optional comma-separated Entra token audiences accepted by the MCP Function App (e.g. api://<agentIdentityBlueprintId>,<agentIdentityBlueprintId>,https://management.azure.com/).')
param mcpAllowedAudiences string = ''
@description('Optional Fabric capacity name (3-63 lowercase letters/digits starting with a letter) to provision or manage in the target resource group.')
param fabricCapacityName string = ''
@description('Fabric capacity SKU when fabricCapacityName is provided (minimum F4).')
param fabricCapacitySku string = 'F4'
@description('Administrator UPN or object ID for the Fabric capacity when fabricCapacityName is provided.')
param fabricCapacityAdmin string = ''

var rgName = !empty(resourceGroupName) ? resourceGroupName : '${resourcePrefix}-rg'
var tags = {
  workload: 'pinned-scoring'
  policySha256: policySha256
  fabricWorkspaceId: fabricWorkspaceId
  fabricLakehouseId: fabricLakehouseId
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'pinned-scoring-resources'
  scope: rg
  params: {
    location: location
    resourcePrefix: resourcePrefix
    foundryProjectName: foundryProjectName
    modelDeploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    deployerPrincipalId: deployerPrincipalId
    deployerClientIp: deployerClientIp
    functionAppName: functionAppName
    functionPlanSku: functionPlanSku
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    existingFoundryAccountName: existingFoundryAccountName
    existingLogAnalyticsName: existingLogAnalyticsName
    existingAppInsightsName: existingAppInsightsName
    mcpAllowedAudiences: mcpAllowedAudiences
    fabricCapacityName: fabricCapacityName
    fabricCapacitySku: fabricCapacitySku
    fabricCapacityAdmin: fabricCapacityAdmin
    policySha256: policySha256
    tags: tags
  }
}

output resourceGroupName string = rg.name
output storageAccountName string = resources.outputs.storageAccountName
output receiptsContainer string = resources.outputs.receiptsContainer
output foundryAccountName string = resources.outputs.foundryAccountName
output foundryEndpoint string = resources.outputs.foundryEndpoint
output foundryProjectName string = resources.outputs.foundryProjectName
output modelDeploymentName string = resources.outputs.modelDeploymentName
output agentIdentityName string = resources.outputs.agentIdentityName
output agentIdentityClientId string = resources.outputs.agentIdentityClientId
output agentIdentityPrincipalId string = resources.outputs.agentIdentityPrincipalId
output functionAppName string = resources.outputs.functionAppName
output functionAppEndpoint string = resources.outputs.functionAppEndpoint
output appInsightsName string = resources.outputs.appInsightsName
output logAnalyticsName string = resources.outputs.logAnalyticsName
