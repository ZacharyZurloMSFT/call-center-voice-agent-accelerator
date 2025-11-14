param environmentName string
param uniqueSuffix string
param identityId string
param tags object

// Voice live api only supported on two regions now 
var location = 'swedencentral'
var aiServicesName = 'aiServices-${environmentName}-${uniqueSuffix}'

@allowed([
  'S0'
])
param sku string = 'S0'

resource aiServices 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: aiServicesName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  sku: {
    name: sku
  }
  kind: 'AIServices'
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    disableLocalAuth: false
    customSubDomainName: 'domain-${environmentName}-${uniqueSuffix}' 
  }
}

output aiServicesEndpoint string = aiServices.properties.endpoint
output aiServicesId string = aiServices.id
output aiServicesName string = aiServices.name
