use anyhow::Result;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

/// LLM 프로바이더 추상화 — OpenAI, Anthropic, Google 등
#[async_trait]
pub trait LlmProvider: Send + Sync {
    /// 프로바이더 이름
    fn name(&self) -> &str;

    /// 비스트리밍 호출
    async fn chat(&self, request: ChatRequest) -> Result<ChatResponse>;

    /// 스트리밍 호출 — 토큰 청크를 채널로 전송
    async fn chat_stream(
        &self,
        request: ChatRequest,
        chunk_tx: mpsc::UnboundedSender<StreamChunk>,
    ) -> Result<ChatResponse>;

    /// 도구 호출 포함 스트리밍
    async fn chat_stream_with_tools(
        &self,
        request: ChatRequest,
        tools: Vec<ToolDefinition>,
        chunk_tx: mpsc::UnboundedSender<StreamChunk>,
    ) -> Result<ChatResponse>;
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    pub system: Option<String>,
    pub temperature: f64,
    pub max_tokens: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<Vec<ToolDefinition>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: MessageContent,
    /// OpenAI 도구 호출 (assistant 메시지에 포함)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
    /// OpenAI 도구 결과 (tool 메시지에 포함)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

impl ChatMessage {
    pub fn user(content: impl Into<String>) -> Self {
        Self { role: "user".to_string(), content: MessageContent::Text(content.into()), tool_calls: None, tool_call_id: None }
    }
    pub fn assistant(content: impl Into<String>) -> Self {
        Self { role: "assistant".to_string(), content: MessageContent::Text(content.into()), tool_calls: None, tool_call_id: None }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum MessageContent {
    Text(String),
    Blocks(Vec<ContentBlock>),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ContentBlock {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "image")]
    Image {
        source: ImageSource,
    },
    #[serde(rename = "tool_use")]
    ToolUse {
        id: String,
        name: String,
        input: serde_json::Value,
    },
    #[serde(rename = "tool_result")]
    ToolResult {
        tool_use_id: String,
        content: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        is_error: Option<bool>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImageSource {
    #[serde(rename = "type")]
    pub source_type: String,   // "base64"
    pub media_type: String,    // "image/png", "image/jpeg" 등
    pub data: String,          // base64 인코딩 데이터
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatResponse {
    pub content: String,
    pub tool_calls: Vec<ToolCall>,
    pub usage: Usage,
    pub stop_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub input: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Usage {
    pub input_tokens: u32,
    pub output_tokens: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub input_schema: serde_json::Value,
}

/// 스트리밍 청크
#[derive(Debug, Clone)]
pub enum StreamChunk {
    /// 텍스트 토큰
    Text(String),
    /// 도구 호출 시작
    ToolCallStart { id: String, name: String },
    /// 도구 호출 입력 (점진적)
    ToolCallDelta { id: String, input_delta: String },
    /// 도구 호출 완료
    ToolCallEnd { id: String },
    /// 스트림 종료
    Done(ChatResponse),
    /// 에러
    Error(String),
}

/// 프로바이더 팩토리
pub fn create_provider(
    provider_name: &str,
    api_key: &str,
    base_url: Option<&str>,
) -> Result<Box<dyn LlmProvider>> {
    match provider_name {
        "anthropic" => Ok(Box::new(crate::llm::anthropic::AnthropicProvider::new(
            api_key.to_string(),
            base_url.map(String::from),
        ))),
        "openai" => Ok(Box::new(crate::llm::openai::OpenAiProvider::new(
            api_key.to_string(),
            base_url.map(String::from),
        ))),
        _ => Err(anyhow::anyhow!("Unknown provider: {}", provider_name)),
    }
}
