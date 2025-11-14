param location string
param keyVaultName string
param tags object
param aiServicesId string
param acsResourceId string
param cosmosAccountId string

var sanitizedKeyVaultName = take(toLower(replace(replace(replace(replace(keyVaultName, '--', '-'), '_', '-'), '[^a-zA-Z0-9-]', ''), '-$', '')), 24)

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: sanitizedKeyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    accessPolicies: []
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource aiServicesKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'AZURE-VOICE-LIVE-API-KEY'
  properties: {
    value: listKeys(aiServicesId, '2023-05-01').key1
  }
}

resource acsConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'ACS-CONNECTION-STRING'
  properties: {
    value: listKeys(acsResourceId, '2025-05-01-preview').primaryConnectionString
  }
}

resource cosmosKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'COSMOS-DB-KEY'
  properties: {
    value: listKeys(cosmosAccountId, '2023-04-15').primaryMasterKey
  }
}

var keyVaultDnsSuffix = environment().suffixes.keyvaultDns

output aiServicesKeySecretUri string = 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/${aiServicesKeySecret.name}'
output acsConnectionStringUri string = 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/${acsConnectionStringSecret.name}'
output cosmosKeySecretUri string = 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/${cosmosKeySecret.name}'
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
