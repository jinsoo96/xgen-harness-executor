use anyhow::Result;
use async_trait::async_trait;
use futures_util::StreamExt;
use reqwest::Client;
use tokio::sync::mpsc;
use tracing::debug;

use super::provider::*;

const DEFAULT_BASE_URL: &str = "https://api.anthropic.com";
const API_VERSION: &str = "2023-06-01";

pub struct AnthropicProvider {
    client: Client,
    api_key: String,
    base_url: String,
}

impl AnthropicProvider {
    pub fn new(api_key: String, base_url: Option<String>) -> Self {
        Self {
            client: Client::new(),
            api_key,
            base_url: base_url.unwrap_or_else(|| DEFAULT_BASE_URL.to_string()),
        }
    }

    fn build_request_body(
        &self,
        request: &ChatRequest,
        stream: bool,
        tools: Option<&[ToolDefinition]>,
    ) -> serde_json::Value {
        let mut body = serde_json::json!({
            "model": request.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        });

        // 시스템 프롬프트
        if let Some(ref system) = request.system {
            body["system"] = serde_json::json!(system);
        }

        // 메시지 변환
        let messages: Vec<serde_json::Value> = request
            .messages
            .iter()
            .map(|msg| {
                serde_json::json!({
                    "role": msg.role,
                    "content": match &msg.content {
                        MessageContent::Text(t) => serde_json::json!(t),
                        MessageContent::Blocks(blocks) => serde_json::json!(blocks),
                    },
                })
            })
            .collect();
        body["messages"] = serde_json::json!(messages);

        // 도구
        if let Some(tools) = tools {
            let tool_defs: Vec<serde_json::Value> = tools
                .iter()
                .map(|t| {
                    serde_json::json!({
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.input_schema,
                    })
                })
                .collect();
            body["tools"] = serde_json::json!(tool_defs);
        }

        body
    }
}

#[async_trait]
impl LlmProvider for AnthropicProvider {
    fn name(&self) -> &str {
        "anthropic"
    }

    async fn chat(&self, request: ChatRequest) -> Result<ChatResponse> {
        let body = self.build_request_body(&request, false, None);

        let resp = self
            .client
            .post(format!("{}/v1/messages", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", API_VERSION)
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow::anyhow!(
                "Anthropic API error {}: {}",
                status.as_u16(),
                text
            ));
        }

        let resp_json: serde_json::Value = resp.json().await?;
        parse_anthropic_response(&resp_json)
    }

    async fn chat_stream(
        &self,
        request: ChatRequest,
        chunk_tx: mpsc::UnboundedSender<StreamChunk>,
    ) -> Result<ChatResponse> {
        self.chat_stream_with_tools(request, vec![], chunk_tx).await
    }

    async fn chat_stream_with_tools(
        &self,
        request: ChatRequest,
        tools: Vec<ToolDefinition>,
        chunk_tx: mpsc::UnboundedSender<StreamChunk>,
    ) -> Result<ChatResponse> {
        let tools_ref = if tools.is_empty() { None } else { Some(tools.as_slice()) };
        let body = self.build_request_body(&request, true, tools_ref);

        let resp = self
            .client
            .post(format!("{}/v1/messages", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", API_VERSION)
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow::anyhow!(
                "Anthropic API error {}: {}",
                status.as_u16(),
                text
            ));
        }

        // SSE 스트림 파싱
        let mut stream = resp.bytes_stream();
        let mut buffer = String::new();
        let mut full_text = String::new();
        let mut tool_calls = Vec::new();
        let mut current_tool_id = String::new();
        let mut current_tool_name = String::new();
        let mut current_tool_input = String::new();
        let mut usage = Usage::default();
        let mut stop_reason = None;

        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            buffer.push_str(&String::from_utf8_lossy(&chunk));

            // SSE 라인 파싱
            while let Some(line_end) = buffer.find('\n') {
                let line = buffer[..line_end].trim().to_string();
                buffer = buffer[line_end + 1..].to_string();

                if !line.starts_with("data: ") {
                    continue;
                }

                let data = &line[6..];
                if data == "[DONE]" {
                    break;
                }

                let event: serde_json::Value = match serde_json::from_str(data) {
                    Ok(v) => v,
                    Err(_) => continue,
                };

                let event_type = event["type"].as_str().unwrap_or("");

                match event_type {
                    "content_block_start" => {
                        let block = &event["content_block"];
                        if block["type"].as_str() == Some("tool_use") {
                            current_tool_id =
                                block["id"].as_str().unwrap_or("").to_string();
                            current_tool_name =
                                block["name"].as_str().unwrap_or("").to_string();
                            current_tool_input.clear();
                            let _ = chunk_tx.send(StreamChunk::ToolCallStart {
                                id: current_tool_id.clone(),
                                name: current_tool_name.clone(),
                            });
                        }
                    }
                    "content_block_delta" => {
                        let delta = &event["delta"];
                        match delta["type"].as_str() {
                            Some("text_delta") => {
                                if let Some(text) = delta["text"].as_str() {
                                    full_text.push_str(text);
                                    let _ = chunk_tx.send(StreamChunk::Text(text.to_string()));
                                }
                            }
                            Some("input_json_delta") => {
                                if let Some(json_str) = delta["partial_json"].as_str() {
                                    current_tool_input.push_str(json_str);
                                    let _ = chunk_tx.send(StreamChunk::ToolCallDelta {
                                        id: current_tool_id.clone(),
                                        input_delta: json_str.to_string(),
                                    });
                                }
                            }
                            _ => {}
                        }
                    }
                    "content_block_stop" => {
                        if !current_tool_id.is_empty() {
                            let input_value: serde_json::Value =
                                serde_json::from_str(&current_tool_input)
                                    .unwrap_or(serde_json::Value::Object(serde_json::Map::new()));
                            tool_calls.push(ToolCall {
                                id: current_tool_id.clone(),
                                name: current_tool_name.clone(),
                                input: input_value,
                            });
                            let _ = chunk_tx.send(StreamChunk::ToolCallEnd {
                                id: current_tool_id.clone(),
                            });
                            current_tool_id.clear();
                        }
                    }
                    "message_delta" => {
                        if let Some(reason) = event["delta"]["stop_reason"].as_str() {
                            stop_reason = Some(reason.to_string());
                        }
                        if let Some(u) = event["usage"].as_object() {
                            if let Some(out) = u.get("output_tokens").and_then(|v| v.as_u64()) {
                                usage.output_tokens = out as u32;
                            }
                        }
                    }
                    "message_start" => {
                        if let Some(u) = event["message"]["usage"].as_object() {
                            if let Some(inp) = u.get("input_tokens").and_then(|v| v.as_u64()) {
                                usage.input_tokens = inp as u32;
                            }
                        }
                    }
                    _ => {
                        debug!(event_type = event_type, "Unhandled SSE event type");
                    }
                }
            }
        }

        let response = ChatResponse {
            content: full_text,
            tool_calls,
            usage,
            stop_reason,
        };

        let _ = chunk_tx.send(StreamChunk::Done(response.clone()));
        Ok(response)
    }
}

fn parse_anthropic_response(resp: &serde_json::Value) -> Result<ChatResponse> {
    let mut content = String::new();
    let mut tool_calls = Vec::new();

    if let Some(blocks) = resp["content"].as_array() {
        for block in blocks {
            match block["type"].as_str() {
                Some("text") => {
                    content.push_str(block["text"].as_str().unwrap_or(""));
                }
                Some("tool_use") => {
                    tool_calls.push(ToolCall {
                        id: block["id"].as_str().unwrap_or("").to_string(),
                        name: block["name"].as_str().unwrap_or("").to_string(),
                        input: block["input"].clone(),
                    });
                }
                _ => {}
            }
        }
    }

    let usage = Usage {
        input_tokens: resp["usage"]["input_tokens"].as_u64().unwrap_or(0) as u32,
        output_tokens: resp["usage"]["output_tokens"].as_u64().unwrap_or(0) as u32,
    };

    Ok(ChatResponse {
        content,
        tool_calls,
        usage,
        stop_reason: resp["stop_reason"].as_str().map(String::from),
    })
}
