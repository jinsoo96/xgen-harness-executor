use anyhow::Result;
use async_trait::async_trait;
use futures_util::StreamExt;
use reqwest::Client;
use tokio::sync::mpsc;

use super::provider::*;

const DEFAULT_BASE_URL: &str = "https://api.openai.com";

pub struct OpenAiProvider {
    client: Client,
    api_key: String,
    base_url: String,
}

impl OpenAiProvider {
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
        // OpenAI 포맷: system 메시지를 messages에 포함
        let mut messages: Vec<serde_json::Value> = vec![];

        if let Some(ref system) = request.system {
            messages.push(serde_json::json!({
                "role": "system",
                "content": system,
            }));
        }

        for msg in &request.messages {
            let content = match &msg.content {
                MessageContent::Text(t) => serde_json::json!(t),
                MessageContent::Blocks(blocks) => {
                    // OpenAI 형식으로 변환
                    let parts: Vec<serde_json::Value> = blocks
                        .iter()
                        .filter_map(|b| match b {
                            ContentBlock::Text { text } => Some(serde_json::json!({
                                "type": "text",
                                "text": text,
                            })),
                            _ => None,
                        })
                        .collect();
                    serde_json::json!(parts)
                }
            };

            // tool 메시지: tool_call_id 포함
            if msg.role == "tool" {
                let mut m = serde_json::json!({
                    "role": "tool",
                    "content": content,
                });
                if let Some(ref id) = msg.tool_call_id {
                    m["tool_call_id"] = serde_json::json!(id);
                }
                messages.push(m);
            // assistant 메시지 + tool_calls: tool_calls 포함
            } else if msg.role == "assistant" && msg.tool_calls.is_some() {
                let tool_calls_json: Vec<serde_json::Value> = msg.tool_calls.as_ref().unwrap()
                    .iter()
                    .map(|tc| serde_json::json!({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": serde_json::to_string(&tc.input).unwrap_or_default(),
                        }
                    }))
                    .collect();
                messages.push(serde_json::json!({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls_json,
                }));
            } else {
                messages.push(serde_json::json!({
                    "role": msg.role,
                    "content": content,
                }));
            }
        }

        let mut body = serde_json::json!({
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": stream,
        });

        if stream {
            body["stream_options"] = serde_json::json!({"include_usage": true});
        }

        if let Some(tools) = tools {
            let tool_defs: Vec<serde_json::Value> = tools
                .iter()
                .map(|t| {
                    serde_json::json!({
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        }
                    })
                })
                .collect();
            body["tools"] = serde_json::json!(tool_defs);
        }

        body
    }
}

#[async_trait]
impl LlmProvider for OpenAiProvider {
    fn name(&self) -> &str {
        "openai"
    }

    async fn chat(&self, request: ChatRequest) -> Result<ChatResponse> {
        let body = self.build_request_body(&request, false, None);

        let resp = self
            .client
            .post(format!("{}/v1/chat/completions", self.base_url))
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow::anyhow!(
                "OpenAI API error {}: {}",
                status.as_u16(),
                text
            ));
        }

        let resp_json: serde_json::Value = resp.json().await?;
        parse_openai_response(&resp_json)
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
            .post(format!("{}/v1/chat/completions", self.base_url))
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow::anyhow!(
                "OpenAI API error {}: {}",
                status.as_u16(),
                text
            ));
        }

        let mut stream = resp.bytes_stream();
        let mut buffer = String::new();
        let mut full_text = String::new();
        let mut tool_calls: Vec<ToolCall> = Vec::new();
        let mut tool_call_args: std::collections::HashMap<usize, String> =
            std::collections::HashMap::new();
        let mut usage = Usage::default();
        let mut stop_reason = None;

        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            buffer.push_str(&String::from_utf8_lossy(&chunk));

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

                // usage (stream_options)
                if let Some(u) = event.get("usage").and_then(|v| v.as_object()) {
                    if let Some(inp) = u.get("prompt_tokens").and_then(|v| v.as_u64()) {
                        usage.input_tokens = inp as u32;
                    }
                    if let Some(out) = u.get("completion_tokens").and_then(|v| v.as_u64()) {
                        usage.output_tokens = out as u32;
                    }
                }

                let choices = match event["choices"].as_array() {
                    Some(c) => c,
                    None => continue,
                };

                for choice in choices {
                    if let Some(reason) = choice["finish_reason"].as_str() {
                        stop_reason = Some(reason.to_string());
                    }

                    let delta = &choice["delta"];

                    // 텍스트 청크
                    if let Some(content) = delta["content"].as_str() {
                        full_text.push_str(content);
                        let _ = chunk_tx.send(StreamChunk::Text(content.to_string()));
                    }

                    // 도구 호출
                    if let Some(tc_array) = delta["tool_calls"].as_array() {
                        for tc in tc_array {
                            let idx = tc["index"].as_u64().unwrap_or(0) as usize;

                            if let Some(func) = tc.get("function") {
                                // 새 도구 호출 시작
                                if let Some(name) = func["name"].as_str() {
                                    let id = tc["id"]
                                        .as_str()
                                        .unwrap_or("")
                                        .to_string();
                                    // 필요시 벡터 확장
                                    while tool_calls.len() <= idx {
                                        tool_calls.push(ToolCall {
                                            id: String::new(),
                                            name: String::new(),
                                            input: serde_json::Value::Null,
                                        });
                                    }
                                    tool_calls[idx].id = id.clone();
                                    tool_calls[idx].name = name.to_string();
                                    tool_call_args.insert(idx, String::new());
                                    let _ = chunk_tx.send(StreamChunk::ToolCallStart {
                                        id,
                                        name: name.to_string(),
                                    });
                                }

                                // 인수 델타
                                if let Some(args) = func["arguments"].as_str() {
                                    tool_call_args
                                        .entry(idx)
                                        .or_default()
                                        .push_str(args);
                                    let id = tool_calls.get(idx)
                                        .map(|tc| tc.id.clone())
                                        .unwrap_or_default();
                                    let _ = chunk_tx.send(StreamChunk::ToolCallDelta {
                                        id,
                                        input_delta: args.to_string(),
                                    });
                                }
                            }
                        }
                    }
                }
            }
        }

        // 도구 호출 인수 파싱
        for (idx, args_str) in &tool_call_args {
            if let Some(tc) = tool_calls.get_mut(*idx) {
                tc.input = serde_json::from_str(args_str)
                    .unwrap_or(serde_json::Value::Object(serde_json::Map::new()));
                let _ = chunk_tx.send(StreamChunk::ToolCallEnd {
                    id: tc.id.clone(),
                });
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

fn parse_openai_response(resp: &serde_json::Value) -> Result<ChatResponse> {
    let choice = &resp["choices"][0];
    let message = &choice["message"];

    let content = message["content"].as_str().unwrap_or("").to_string();

    let mut tool_calls = Vec::new();
    if let Some(tc_array) = message["tool_calls"].as_array() {
        for tc in tc_array {
            tool_calls.push(ToolCall {
                id: tc["id"].as_str().unwrap_or("").to_string(),
                name: tc["function"]["name"].as_str().unwrap_or("").to_string(),
                input: serde_json::from_str(
                    tc["function"]["arguments"].as_str().unwrap_or("{}"),
                )
                .unwrap_or(serde_json::Value::Object(serde_json::Map::new())),
            });
        }
    }

    let usage = Usage {
        input_tokens: resp["usage"]["prompt_tokens"].as_u64().unwrap_or(0) as u32,
        output_tokens: resp["usage"]["completion_tokens"].as_u64().unwrap_or(0) as u32,
    };

    Ok(ChatResponse {
        content,
        tool_calls,
        usage,
        stop_reason: choice["finish_reason"].as_str().map(String::from),
    })
}
