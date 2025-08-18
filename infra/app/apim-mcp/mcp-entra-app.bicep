extension microsoftGraphV1

@description('The name of the MCP Entra application')
param mcpAppUniqueName string

@description('The display name of the MCP Entra application')
param mcpAppDisplayName string

@description('Tenant ID where the application is registered')
param tenantId string = tenant().tenantId

@description('The principle id of the user-assigned managed identity')
param userAssignedIdentityPrincipleId string

@description('The web app name for callback URL configuration')
param functionAppName string

@description('Provide an array of Microsoft Graph scopes like "User.Read"')
param appScopes array = ['User.Read']

var loginEndpoint = environment().authentication.loginEndpoint
var issuer = '${loginEndpoint}${tenantId}/v2.0'

// Microsoft Graph app ID
var graphAppId = '00000003-0000-0000-c000-000000000000'
var msGraphAppId = graphAppId

// Get the Microsoft Graph service principal so that the scope names
// can be looked up and mapped to a permission ID
resource msGraphSP 'Microsoft.Graph/servicePrincipals@v1.0' existing = {
  appId: graphAppId
}

var graphScopes = msGraphSP.oauth2PermissionScopes

var permissionId = guid(mcpAppUniqueName, 'user_impersonate')
resource mcpEntraApp 'Microsoft.Graph/applications@v1.0' = {
  displayName: mcpAppDisplayName
  uniqueName: mcpAppUniqueName
  api: {
    oauth2PermissionScopes: [
      {
        id: permissionId
        adminConsentDescription: 'Allows the application to access MCP resources on behalf of the signed-in user'
        adminConsentDisplayName: 'Access MCP resources'
        isEnabled: true
        type: 'User'
        userConsentDescription: 'Allows the app to access MCP resources on your behalf'
        userConsentDisplayName: 'Access MCP resources'
        value: 'user_impersonate'
      }
    ]
    requestedAccessTokenVersion: 2
    preAuthorizedApplications: [
      {
        appId: 'aebc6443-996d-45c2-90f0-388ff96faa56'
        delegatedPermissionIds: [
          guid(mcpAppUniqueName, 'user_impersonate')
        ]
      }
    ]
  }
  // Parameterized Microsoft Graph delegated scopes based on appScopes
  requiredResourceAccess: [
    {
      resourceAppId: msGraphAppId // Microsoft Graph
      resourceAccess: [
        for (scope, i) in appScopes: {
          id: filter(graphScopes, graphScopes => graphScopes.value == scope)[0].id
          type: 'Scope'
        }
      ]
    }
  ]
  spa: {
    redirectUris: [
      'https://${functionAppName}.azurewebsites.net/auth/callback'
    ]
  }

  resource fic 'federatedIdentityCredentials@v1.0' = {
    name: '${mcpEntraApp.uniqueName}/msiAsFic'
    description: 'Trust the user-assigned MI as a credential for the MCP app'
    audiences: [
       'api://AzureADTokenExchange'
    ]
    issuer: issuer
    subject: userAssignedIdentityPrincipleId
  }
}

resource applicationRegistrationServicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: mcpEntraApp.appId
}

// Outputs
output mcpAppId string = mcpEntraApp.appId
output mcpAppTenantId string = tenantId
