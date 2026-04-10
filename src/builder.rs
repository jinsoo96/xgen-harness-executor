//! HarnessBuilder — HTTP 서버 없이 임베드 가능한 실행기 API
//!
//! 다른 Rust 서비스에서 harness-executor를 라이브러리로 사용할 때 진입점.
//!
//! # 사용 예
//!
//! ```rust,ignore
//! use xgen_harness_executor::builder::HarnessBuilder;
//!
//! let output = HarnessBuilder::new()
//!     .provider("openai", "gpt-4o-mini")
//!     .api_key("sk-...")
//!     .text("피보나치 함수를 작성해줘")
//!     .stages(["init", "execute", "complete"])
//!     .run()
//!     .await?;
//!
//! println!("{}", output);
//! ```

use std::sync::Arc;

use anyhow::Result;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use crate::events::SseEvent;
use crate::llm::provider::create_provider;
use crate::state_machine::agent_executor::{AgentConfig, AgentStateMachine};
use crate::state_machine::stage::HarnessStage;

/// 하네스 실행기 빌더.
/// HTTP 서버 없이 임베드 사용을 위한 간단한 API.
pub struct HarnessBuilder {
    text: String,
    system_prompt: String,
    provider_name: String,
    model: String,
    max_tokens: u32,
    temperature: f64,
    eval_threshold: f64,
    max_retries: u32,
    stages: Vec<HarnessStage>,
    tools: Vec<String>,
    modules: Vec<String>,
    /// API 키 직접 주입 (없으면 환경변수에서 읽음)
    api_key: Option<String>,
    base_url: Option<String>,
}

impl Default for HarnessBuilder {
    fn default() -> Self {
        Self {
            text: String::new(),
            system_prompt: String::new(),
            provider_name: "anthropic".to_string(),
            model: "claude-sonnet-4-6".to_string(),
            max_tokens: 8192,
            temperature: 0.7,
            eval_threshold: 0.7,
            max_retries: 3,
            stages: vec![HarnessStage::Init, HarnessStage::Execute, HarnessStage::Complete],
            tools: vec![],
            modules: vec![],
            api_key: None,
            base_url: None,
        }
    }
}

impl HarnessBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn text(mut self, text: impl Into<String>) -> Self {
        self.text = text.into();
        self
    }

    pub fn system_prompt(mut self, prompt: impl Into<String>) -> Self {
        self.system_prompt = prompt.into();
        self
    }

    /// provider: "anthropic" | "openai" | "google", model: 모델 ID
    pub fn provider(mut self, provider: impl Into<String>, model: impl Into<String>) -> Self {
        self.provider_name = provider.into();
        self.model = model.into();
        self
    }

    pub fn max_tokens(mut self, n: u32) -> Self {
        self.max_tokens = n;
        self
    }

    pub fn temperature(mut self, t: f64) -> Self {
        self.temperature = t;
        self
    }

    pub fn eval_threshold(mut self, t: f64) -> Self {
        self.eval_threshold = t;
        self
    }

    pub fn max_retries(mut self, n: u32) -> Self {
        self.max_retries = n;
        self
    }

    /// 단계 목록. 예: `["init", "execute", "validate", "decide", "complete"]`
    pub fn stages<S: AsRef<str>>(mut self, stages: impl IntoIterator<Item = S>) -> Self {
        self.stages = stages
            .into_iter()
            .filter_map(|s| HarnessStage::from_str(s.as_ref()))
            .collect();
        self
    }

    pub fn tools(mut self, tools: impl IntoIterator<Item = impl Into<String>>) -> Self {
        self.tools = tools.into_iter().map(|t| t.into()).collect();
        self
    }

    pub fn modules(mut self, modules: impl IntoIterator<Item = impl Into<String>>) -> Self {
        self.modules = modules.into_iter().map(|m| m.into()).collect();
        self
    }

    /// API 키를 직접 주입 (없으면 환경변수 ANTHROPIC_API_KEY / OPENAI_API_KEY 사용)
    pub fn api_key(mut self, key: impl Into<String>) -> Self {
        self.api_key = Some(key.into());
        self
    }

    /// 커스텀 베이스 URL (vLLM, Azure 등 호환 엔드포인트)
    pub fn base_url(mut self, url: impl Into<String>) -> Self {
        self.base_url = Some(url.into());
        self
    }

    /// SSE 이벤트를 콜백으로 수신하며 실행. 완료 시 최종 텍스트 반환.
    pub async fn run_with_events<F>(self, mut on_event: F) -> Result<String>
    where
        F: FnMut(SseEvent) + Send + 'static,
    {
        // API 키: 직접 주입 > 환경변수
        let api_key = if let Some(k) = self.api_key {
            k
        } else {
            let env_key = match self.provider_name.as_str() {
                "anthropic" => "ANTHROPIC_API_KEY",
                "openai" => "OPENAI_API_KEY",
                "google" => "GOOGLE_API_KEY",
                _ => "API_KEY",
            };
            std::env::var(env_key).unwrap_or_default()
        };

        let provider = create_provider(
            &self.provider_name,
            &api_key,
            self.base_url.as_deref(),
        )?;

        let (tx, mut rx) = mpsc::unbounded_channel::<SseEvent>();
        let cancel_token = CancellationToken::new();

        let config = AgentConfig {
            provider_name: self.provider_name,
            model: self.model,
            system_prompt: self.system_prompt,
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            max_retries: self.max_retries,
            eval_threshold: self.eval_threshold,
            context_budget: 200_000,
            tools: self.tools,
            modules: self.modules,
        };

        let input = serde_json::json!({ "text": self.text });

        // 이벤트 수신 태스크
        let event_handle = tokio::spawn(async move {
            while let Some(event) = rx.recv().await {
                on_event(event);
            }
        });

        let mut machine = AgentStateMachine::new(
            Uuid::new_v4().to_string(),
            "harness-embedded".to_string(),
            self.stages,
            config,
            Arc::from(provider),
            tx,
            cancel_token,
        );

        let result = machine.run(input).await?;
        event_handle.await.ok();

        // 최종 텍스트 추출
        let text = result
            .get("text")
            .and_then(|v| v.as_str())
            .or_else(|| result.get("output").and_then(|v| v.as_str()))
            .unwrap_or("")
            .to_string();

        Ok(text)
    }

    /// 이벤트 없이 단순 실행. 완료 시 최종 텍스트 반환.
    pub async fn run(self) -> Result<String> {
        self.run_with_events(|_| {}).await
    }
}
