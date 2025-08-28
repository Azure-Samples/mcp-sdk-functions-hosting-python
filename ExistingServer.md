# Host your existing MCP server on Azure Functions
If you have already have a server built with [Anthropic's MCP SDKs](https://github.com/modelcontextprotocol/servers?tab=readme-ov-file#model-context-protocol-servers), this document provides guidance on how to prepare the MCP server for deployment as a custom handler on Azure Functions. Only **stateless** servers that use the **streamable http** transport are supported at the moment.  

## Prerequisites
The guidance below uses the following: 

* [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local?tabs=windows%2Cisolated-process%2Cnode-v4%2Cpython-v2%2Chttp-trigger%2Ccontainer-apps&pivots=programming-language-typescript) 
* [Visual Studio Code](https://code.visualstudio.com/) 

## Approach 1: Use experimental prompt
Try out the experimental [Azure Functions MCP server deployment helper](https://aka.ms/mcp-deployment-helper) to have VSCode's Copilot prepare your server for custom handler deployment. The deployment helpder contains a custom prompt that provides Copilot instructions. 

## Approach 2: Manually add required artifacts 
1. In the root directory of your MCP server project, create a `host.json` with the following:
    ```json
    {
        "version": "2.0",
        "extensions": {
            "http": {
                "routePrefix": ""
            }
        },
        "customHandler": {
            "description": {
                "defaultExecutablePath": "python",
                "workingDirectory": "",
                "arguments": ["<path to the main script (e.g., weather.py)>"]
            },
            "enableForwardingHttpRequest": true,
            "enableHttpProxyingRequest": true
        }
    }
    ```

1. Create a folder named `mcp-handler` in the root directory. Inside the folder, create a file named `function.json` with the following:
    ```json
    {
        "bindings": [
            {
                "authLevel": "function",
                "type": "httpTrigger",
                "direction": "in",
                "name": "req",
                "methods": ["get", "post", "put", "delete", "patch", "head", "options"],
                "route": "{*route}"
            },
            {
                "type": "http",
                "direction": "out",
                "name": "res"
            }
        ]
    }
    ```
    This file marks the MCP server as an HTTP trigger to the Functions host, allowing access to the server through an HTTP endpoint. Functions allows you to use access keys to make it harder to access function endpoints. In this case, the line `"authLevel": "function"` specifies that a key must be included in the request when accessing the MCP server. 

1. Again in the root directory, create a `local.settings.json` file with the following:

    ```json
    {
        "IsEncrypted": false,
        "Values": {
            "FUNCTIONS_WORKER_RUNTIME": "custom"
        }
    }
    ```
    This file is where all the environment variables are kept. 

1. Modify the MCP server code to listen for HTTP requests on the port specified by the `FUNCTIONS_CUSTOMHANDLER_PORT` environment variable. This is the only line of code that needs modification:

    ```py
    mcp_port = int(os.environ.get("FUNCTIONS_CUSTOMHANDLER_PORT", 8080))
    mcp = FastMCP("weather", stateless_http=True, port=mcp_port)
    ```
1. A `requirements.txt` is required today. Make sure there is one is the root directory with the following:
    ```txt
    httpx
    mcp[cli]>=1.5.0
    ```

That's it! You're ready to run your MCP server locally and deploy to Azure Functions as a custom handler. 

## Run the server locally
1. Create a virtual environment and install the packages in `requirements.txt`. On Visual Studio Code, this can be done easily by opening command palette (`Ctrl+Shift+P` / `Cmd+Shift+P`), searching for **Python: Create Environment**, and selecting **Venv**
1. In the root directory, activate the virtual environment
   
    **macOS/Linux:**
    ```bash
    source .venv/bin/activate
    ```
   **Windows Command Prompt:**
   ```cmd
   .venv\Scripts\activate.bat
   ```
1. Run `func start` to start the MCP server locally

### Connect to local server
1. Open the command palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and search for **MCP: Add server**
1. Choose **HTTP**
1. Enter `http://0.0.0.0:7071/mcp`. This will create a *mcp.json* file (under the *.vscode* directory) that looks similar to the following:
    ```json
    {
        "servers": {
            "local-mcp-server": {
                "type": "http",
                "url": "http://0.0.0.0:7071/mcp"
            }
        },
        "inputs": []
    }
1. Click the Start button above **local-mcp-server**

## Deploy MCP server to Azure Functions
1. [Create a Function app](https://learn.microsoft.com/azure/azure-functions/functions-create-function-app-portal?tabs=core-tools&pivots=flex-consumption-plan) hosted on the **Flex Consumption plan** and related resources. 
    - Choose **Python 3.12** as the runtime stack and version. 
    - On *Networking* tab, choose "Enable public access" to allow all IPs to access the app. This helps with the deployment step and allows for accessing the app (i.e. server) during testing. For production scenarios, it's recommended that you configure IP allowlist or set up VNET instead.
1. Add the `PYTHONPATH` app setting:
    ```azcli
    az functionapp config appsettings set --name <function app name> --resource-group <resource group name> --settings PYTHONPATH='/home/site/wwwroot/.python_packages/lib/site-packages'
    ```
1. Open the command palette and search for **Azure Functions: Deploy to Function app**. 
1. Choose your Azure subscription and the function app created in Step 1. Select **Deploy** when prompted.

### Connect to remote server
1. Add the following section to your *mcp.json*, replacing `functionapp-name` with the app you created:
    ```json
    "remote-mcp-server": {
        "type": "http",
        "url": "https://{functionapp-name}.azurewebsites.net/mcp",
        "headers": {
            "x-functions-key": "{access key}"
        }
    }
    ```
1. Go to the Function App resource on Azure portal
1. On the left menu, click on **Functions** -> **App keys**
1. Copy the *default* key
1. Open *mcp.json* and paste the access key as the `x-functions-key` value. Start the server.
