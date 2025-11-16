import json
import os
import sys
from typing import Any

import httpx
from azure.identity import ManagedIdentityCredential, OnBehalfOfCredential
from mcp.server import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.types import Tool, TextContent, CallToolResult

# Create an MCP server
server = Server("get-user")

# Store request headers for access in tool handlers
_request_headers = {}


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_current_user",
            description="Get current logged-in user information from Microsoft Graph using Azure App Service authentication headers and On-Behalf-Of flow",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            },
        )
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle tool execution."""
    if name != "get_current_user":
        raise ValueError(f"Unknown tool: {name}")

    try:
        global _request_headers
        
        if not _request_headers:
            result = {
                "authenticated": False,
                "message": "No authentication headers found. This tool requires Azure App Service authentication."
            }
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])

        # Get the auth token from Authorization header and remove the "Bearer " prefix
        auth_header = _request_headers.get("authorization", "")
        if not auth_header or not auth_header.startswith("Bearer "):
            result = {
                "authenticated": False,
                "message": "No bearer token found in authorization header."
            }
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])
        
        auth_token = auth_header.split(" ", 1)[1]

        # Get configuration from environment variables
        token_exchange_audience = os.environ.get("TokenExchangeAudience", "api://AzureADTokenExchange")
        public_token_exchange_scope = f"{token_exchange_audience}/.default"
        federated_credential_client_id = os.environ.get("OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID")
        client_id = os.environ.get("WEBSITE_AUTH_CLIENT_ID")
        tenant_id = os.environ.get("WEBSITE_AUTH_AAD_ALLOWED_TENANTS")

        if not all([federated_credential_client_id, client_id, tenant_id]):
            result = {
                "authenticated": False,
                "message": "Missing required environment variables for OBO flow. Ensure OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID, WEBSITE_AUTH_CLIENT_ID, and WEBSITE_AUTH_AAD_ALLOWED_TENANTS are set."
            }
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])

        # Create Managed Identity credential
        managed_identity_credential = ManagedIdentityCredential(client_id=federated_credential_client_id)
        
        # Get assertion token for OBO flow
        assertion_token_result = managed_identity_credential.get_token(public_token_exchange_scope)
        assertion_token = assertion_token_result.token

        # Create OBO credential
        obo_credential = OnBehalfOfCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            user_assertion=auth_token,
            client_credential=assertion_token
        )

        # Get token for Microsoft Graph
        graph_token_result = obo_credential.get_token("https://graph.microsoft.com/.default")
        graph_token = graph_token_result.token

        # Call Microsoft Graph API to get user information
        async with httpx.AsyncClient() as client:
            graph_response = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={
                    "Authorization": f"Bearer {graph_token}"
                }
            )
            graph_response.raise_for_status()
            graph_data = graph_response.json()

        # Mask sensitive information (for demo purposes)
        masked_user_data = dict(graph_data)
        if "businessPhones" in masked_user_data:
            masked_user_data["businessPhones"] = ["[MASKED]" for _ in masked_user_data["businessPhones"]]
        if "id" in masked_user_data:
            masked_user_data["id"] = "[MASKED]"

        result = {
            "authenticated": True,
            "user": masked_user_data,
            "message": "Successfully retrieved user information from Microsoft Graph"
        }

        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        hostname = os.environ.get("WEBSITE_HOSTNAME", "your-function-app-hostname")
        
        error_result = {
            "authenticated": False,
            "message": f"Error during token exchange and Graph API call. You're logged in but might need to grant consent to the application. Open a browser to the following link to consent: https://{hostname}/.auth/login/aad?post_login_redirect_uri=https://{hostname}/",
            "error": str(e),
            "details": error_details
        }
        
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(error_result, indent=2))])


async def main():
    """Run the MCP server using streamable HTTP transport."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route
    import uvicorn
    
    async def handle_mcp(request: Request) -> Response:
        """Handle MCP requests."""
        global _request_headers
        # Store request headers so tools can access them
        _request_headers = dict(request.headers)
        
        # Create transport for this request
        transport = StreamableHTTPServerTransport()
        await server.connect(transport)
        
        # Get the request body
        body = await request.body()
        
        # Handle the MCP request
        result = await transport.handle_request(body.decode() if body else "")
        
        return Response(
            content=result,
            media_type="application/json"
        )
    
    # Create Starlette app
    app = Starlette(
        routes=[
            Route("/mcp", handle_mcp, methods=["POST"]),
        ]
    )
    
    # Run with uvicorn
    port = int(os.environ.get("FUNCTIONS_CUSTOMHANDLER_PORT", "8000"))
    print(f"Starting MCP get_user server on port {port}...")
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server_instance = uvicorn.Server(config)
    await server_instance.serve()


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Error while running MCP server: {e}", file=sys.stderr)
        sys.exit(1)
