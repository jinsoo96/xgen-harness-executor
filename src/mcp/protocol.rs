use serde::{Deserialize, Serialize};

/// JSON-RPC 2.0 요청
#[derive(Debug, Serialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub id: u64,
    pub method: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Value>,
}

/// JSON-RPC 2.0 응답
#[derive(Debug, Deserialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    pub id: Option<u64>,
    pub result: Option<serde_json::Value>,
    pub error: Option<JsonRpcError>,
}

#[derive(Debug, Deserialize)]
pub struct JsonRpcError {
    pub code: i32,
    pub message: String,
    pub data: Option<serde_json::Value>,
}

/// MCP Initialize 요청 파라미터
#[derive(Debug, Serialize)]
pub struct InitializeParams {
    #[serde(rename = "protocolVersion")]
    pub protocol_version: String,
    pub capabilities: ClientCapabilities,
    #[serde(rename = "clientInfo")]
    pub client_info: ClientInfo,
}

#[derive(Debug, Serialize)]
pub struct ClientCapabilities {
    pub roots: RootsCapability,
    pub sampling: serde_json::Value,
}

#[derive(Debug, Serialize)]
pub struct RootsCapability {
    #[serde(rename = "listChanged")]
    pub list_changed: bool,
}

#[derive(Debug, Serialize)]
pub struct ClientInfo {
    pub name: String,
    pub version: String,
}

impl JsonRpcRequest {
    pub fn new(id: u64, method: &str, params: Option<serde_json::Value>) -> Self {
        Self {
            jsonrpc: "2.0".to_string(),
            id,
            method: method.to_string(),
            params,
        }
    }

    /// MCP initialize 요청
    pub fn initialize(id: u64) -> Self {
        Self::new(
            id,
            "initialize",
            Some(serde_json::to_value(InitializeParams {
                protocol_version: "2024-11-05".to_string(),
                capabilities: ClientCapabilities {
                    roots: RootsCapability { list_changed: true },
                    sampling: serde_json::json!({}),
                },
                client_info: ClientInfo {
                    name: "xgen-harness-executor".to_string(),
                    version: "0.1.0".to_string(),
                },
            }).unwrap()),
        )
    }

    /// tools/list 요청
    pub fn tools_list(id: u64) -> Self {
        Self::new(id, "tools/list", None)
    }

    /// tools/call 요청
    pub fn tools_call(id: u64, name: &str, arguments: serde_json::Value) -> Self {
        Self::new(
            id,
            "tools/call",
            Some(serde_json::json!({
                "name": name,
                "arguments": arguments,
            })),
        )
    }
}
