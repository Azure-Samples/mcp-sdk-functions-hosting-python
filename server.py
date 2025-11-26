import os
import sys
import logging
from typing import Any
from pathlib import Path

import httpx
from azure.identity import OnBehalfOfCredential, ManagedIdentityCredential
from mcp.server.fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from starlette.requests import Request
from starlette.responses import HTMLResponse

# Reduce MCP SDK, uvicorn, and httpx logging verbosity
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Initialize FastMCP server
mcp = FastMCP("weather", stateless_http=True)

# Constants
NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"


async def make_nws_request(url: str) -> dict[str, Any] | None:
    """Make a request to the NWS API with proper error handling."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None


def format_alert(feature: dict) -> str:
    """Format an alert feature into a readable string."""
    props = feature["properties"]
    return f"""
Event: {props.get('event', 'Unknown')}
Area: {props.get('areaDesc', 'Unknown')}
Severity: {props.get('severity', 'Unknown')}
Description: {props.get('description', 'No description available')}
Instructions: {props.get('instruction', 'No specific instructions provided')}
"""


@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get weather alerts for a US state.

    Args:
        state: Two-letter US state code (e.g. CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."

    if not data["features"]:
        return "No active alerts for this state."

    alerts = [format_alert(feature) for feature in data["features"]]
    return "\n---\n".join(alerts)


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    # First get the forecast grid endpoint
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # Get the forecast URL from the points response
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    # Format the periods into a readable forecast
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # Only show next 5 periods
        forecast = f"""
{period['name']}:
Temperature: {period['temperature']}°{period['temperatureUnit']}
Wind: {period['windSpeed']} {period['windDirection']}
Forecast: {period['detailedForecast']}
"""
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)

@mcp.tool()
async def get_user_info() -> str:
    """
    Demonstrate extracting the bearer token (for OBO flow) from the incoming Authorization header.

    Args:
        authorization: Authorization header value that gets passed from the MCP client when it calls this tool

    Returns:
        String with user info or error message.
    """
    request = get_http_request()

    auth_header = request.headers.get("authorization", "")
    
    if not auth_header:
        return "Error: No access token found in request"
    
    # Extract bearer token (remove "Bearer " prefix if present)
    access_token = auth_header.replace("Bearer ", "").replace("bearer ", "").strip()
        
   # Get required environment variables
    token_exchange_audience = os.environ.get("TokenExchangeAudience", "api://AzureADTokenExchange")
    public_token_exchange_scope = f"{token_exchange_audience}/.default"
    federated_credential_client_id = os.environ.get("OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID")
    client_id = os.environ.get("WEBSITE_AUTH_CLIENT_ID")
    tenant_id = os.environ.get("WEBSITE_AUTH_AAD_ALLOWED_TENANTS")
    
    try:
        # Create managed identity credential for getting the client assertion
        managed_identity_credential = ManagedIdentityCredential(client_id=federated_credential_client_id)
        
        # Get the client assertion token first
        client_assertion_token = managed_identity_credential.get_token(public_token_exchange_scope)
        
        # Use OBO credential with managed identity assertion
        obo_credential = OnBehalfOfCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            user_assertion=access_token,
            client_assertion_func=lambda: client_assertion_token.token
        )
        
        # Get token for Microsoft Graph
        graph_token = obo_credential.get_token("https://graph.microsoft.com/.default")
        logging.info("Successfully obtained Graph token")
        
        # Call Microsoft Graph API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {graph_token.token}"}
            )
            response.raise_for_status()
            user_data = response.json()
            
            logging.info(f"Successfully retrieved user info for: {user_data.get('userPrincipalName', 'N/A')}")
            
            return f"""User Information:
- Display Name: {user_data.get('displayName', 'N/A')}
- Email: {user_data.get('mail', 'N/A')}
- User Principal Name: {user_data.get('userPrincipalName', 'N/A')}
- ID: {user_data.get('id', 'N/A')}"""
            
    except Exception as e:
        logging.error(f"Error getting user info: {str(e)}", exc_info=True)
        website_hostname = os.environ.get('WEBSITE_HOSTNAME', '')
        return f"""Error getting user info: {str(e)}

    You're logged in but might need to grant consent to the application.
    Open a browser to the following link to consent:
    https://{website_hostname}/.auth/login/aad?post_login_redirect_uri=https://{website_hostname}/authcomplete"""

# Add a custom route to serve authcomplete.html
@mcp.custom_route("/authcomplete", methods=["GET"])
async def auth_complete(request: Request) -> HTMLResponse:
    """Serve the authcomplete.html file after OAuth redirect."""
    try:
        html_path = Path(__file__).parent / "authcomplete.html"
        logging.info(f"Complete authcomplete.html: {html_path}")
        
        content = html_path.read_text()
        return HTMLResponse(content=content, status_code=200)
    except Exception as e:
        logging.error(f"Error loading authcomplete.html: {str(e)}", exc_info=True)
        return HTMLResponse(
            content="<html><body><h1>Authentication Complete</h1><p>You can close this window.</p></body></html>", 
            status_code=200
        )

if __name__ == "__main__":
    try:
        # Initialize and run the server
        print("Starting MCP server...")
        mcp.run(transport="streamable-http") 
    except Exception as e:
        print(f"Error while running MCP server: {e}", file=sys.stderr)
