param identityPrincipalId string
param aiServicesId string
param keyVaultName string
param cosmosAccountId string

resource aiServicesResource 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: last(split(aiServicesId, '/'))
}

resource aiServicesRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServicesId, identityPrincipalId, 'Cognitive Services User')
  scope: aiServicesResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource aiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServicesId, identityPrincipalId, 'ai-reader')
  scope: aiServicesResource
  properties: {
    principalId: identityPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
    principalType: 'ServicePrincipal'
  }
}


resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' existing = {
  name: keyVaultName
}

resource keyVaultRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identityPrincipalId, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2023-04-15' existing = {
  name: last(split(cosmosAccountId, '/'))
}

resource cosmosRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cosmosAccount.id, identityPrincipalId, 'Cosmos DB Built-in Data Contributor')
  scope: cosmosAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '00000000-0000-0000-0000-000000000002')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
