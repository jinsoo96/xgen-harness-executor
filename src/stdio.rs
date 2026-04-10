//! JSON-RPC 2.0 프로토콜 타입 — stdin/stdout CLI 통신용
//!
//! Python 측에서 subprocess로 실행 후 stdin에 요청을 쓰고,
//! stdout에서 이벤트 알림 + 최종 응답을 라인별로 읽는다.

use serde::{Deserialize, Serialize};

// ── JSON-RPC 요청 ────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub id: serde_json::Value,
    pub method: String,
    #[serde(default)]
    pub params: serde_json::Value,
}

// ── JSON-RPC 응답 (성공) ─────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: &'static str,
    pub id: serde_json::Value,
    pub result: serde_json::Value,
}

impl JsonRpcResponse {
    pub fn ok(id: serde_json::Value, result: serde_json::Value) -> Self {
        Self {
            jsonrpc: "2.0",
            id,
            result,
        }
    }
}

// ── JSON-RPC 응답 (에러) ─────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct JsonRpcError {
    pub jsonrpc: &'static str,
    pub id: serde_json::Value,
    pub error: JsonRpcErrorBody,
}

#[derive(Debug, Serialize)]
pub struct JsonRpcErrorBody {
    pub code: i32,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<String>,
}

impl JsonRpcError {
    pub fn new(id: serde_json::Value, code: i32, message: impl Into<String>) -> Self {
        Self {
            jsonrpc: "2.0",
            id,
            error: JsonRpcErrorBody {
                code,
                message: message.into(),
                data: None,
            },
        }
    }

    pub fn parse_error() -> Self {
        Self::new(serde_json::Value::Null, -32700, "Parse error")
    }

    pub fn method_not_found(id: serde_json::Value) -> Self {
        Self::new(id, -32601, "Method not found")
    }

    pub fn execution_error(id: serde_json::Value, msg: impl Into<String>) -> Self {
        Self::new(id, -32000, msg)
    }
}

// ── JSON-RPC 알림 (이벤트 스트리밍용, id 없음) ───────────────

#[derive(Debug, Serialize)]
pub struct JsonRpcNotification {
    pub jsonrpc: &'static str,
    pub method: &'static str,
    pub params: serde_json::Value,
}

impl JsonRpcNotification {
    pub fn event(event_type: &str, data: serde_json::Value) -> Self {
        Self {
            jsonrpc: "2.0",
            method: "harness/event",
            params: serde_json::json!({
                "event": event_type,
                "data": data,
            }),
        }
    }
}

// ── harness/run 파라미터 ─────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct HarnessRunParams {
    pub text: String,
    #[serde(default = "default_provider")]
    pub provider: String,
    #[serde(default = "default_model")]
    pub model: String,
    #[serde(default)]
    pub api_key: Option<String>,
    #[serde(default)]
    pub system_prompt: Option<String>,
    #[serde(default)]
    pub stages: Option<Vec<String>>,
    #[serde(default)]
    pub tools: Option<Vec<String>>,
    #[serde(default = "default_temperature")]
    pub temperature: f64,
    #[serde(default = "default_max_tokens")]
    pub max_tokens: u32,
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    #[serde(default = "default_eval_threshold")]
    pub eval_threshold: f64,
}

fn default_provider() -> String { "anthropic".to_string() }
fn default_model() -> String { "claude-sonnet-4-6".to_string() }
fn default_temperature() -> f64 { 0.7 }
fn default_max_tokens() -> u32 { 8192 }
fn default_max_retries() -> u32 { 3 }
fn default_eval_threshold() -> f64 { 0.7 }
