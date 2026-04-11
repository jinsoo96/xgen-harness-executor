//! # xgen-harness-executor
//!
//! XGEN Harness Executor — Rust 상태 머신 기반 에이전트 실행기
//!
//! ## Feature Flags
//!
//! - `core` (default): 상태 머신 + LLM + MCP + Builder — 다른 서비스에 임베드 시 사용
//! - `server`: HTTP 서버 (axum, JWT, DB 마이그레이션) — 독립 서비스로 실행 시 사용
//!
//! ## 라이브러리로 사용
//!
//! ```toml
//! [dependencies]
//! xgen-harness-executor = { path = "...", default-features = false, features = ["core"] }
//! ```
//!
//! ```rust,ignore
//! use xgen_harness_executor::prelude::*;
//!
//! let output = HarnessBuilder::new()
//!     .provider("openai", "gpt-4o-mini")
//!     .text("안녕하세요")
//!     .run()
//!     .await?;
//! ```

#![allow(dead_code)]

// ── Core (항상 포함) ─────────────────────────────────────────
pub mod events;
pub mod builder;
pub mod context;
pub mod llm;
pub mod mcp;
pub mod stages;
pub mod state_machine;
pub mod tools;
pub mod workflow;

// ── Server (feature = "server" 일 때만) ──────────────────────
#[cfg(feature = "server")]
pub mod api;

// ── stdio JSON-RPC (feature = "stdio" 일 때만) ──────────────
#[cfg(feature = "stdio")]
pub mod stdio;

/// 자주 쓰는 타입 한 번에 임포트
pub mod prelude {
    pub use crate::events::SseEvent;
    pub use crate::builder::HarnessBuilder;
    pub use crate::state_machine::agent_executor::{AgentConfig, AgentStateMachine, ExecutionContext};
    pub use crate::state_machine::orchestrator::{AgentConnection, AgentDefinition, OrchestrationPattern};
    pub use crate::state_machine::stage::{HarnessStage, StageResult};
    pub use crate::llm::provider::{
        ChatMessage, ChatRequest, ChatResponse, LlmProvider, MessageContent,
        ToolCall, ToolDefinition, create_provider,
    };
    pub use crate::workflow::converter::{convert_legacy_to_harness, LegacyWorkflow};
    pub use crate::workflow::definition::HarnessWorkflow;
    pub use crate::mcp::client::{McpClientManager, McpTransport};
    pub use crate::tools::registry::ToolRegistry;
}
