param location string
param environmentName string
param uniqueSuffix string
param tags object = {}

// Helper to sanitize environmentName for valid cosmos db name
var sanitizedEnvName = toLower(replace(replace(replace(replace(environmentName, ' ', ''), '--', ''), '[^a-zA-Z0-9-]', ''), '_', ''))
var cosmosAccountName = take('cosmos-${sanitizedEnvName}-${uniqueSuffix}', 44)
var databaseName = 'conversationdb'
var containerName = 'transcripts'

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2023-04-15' = {
  name: cosmosAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    enableFreeTier: false
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    publicNetworkAccess: 'Enabled'
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2023-04-15' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-04-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: ['/sessionId']
        kind: 'Hash'
      }
      defaultTtl: -1 // No TTL by default
    }
  }
}

output cosmosAccountName string = cosmosAccount.name
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint
output databaseName string = databaseName
output containerName string = containerName
output cosmosAccountId string = cosmosAccount.id
