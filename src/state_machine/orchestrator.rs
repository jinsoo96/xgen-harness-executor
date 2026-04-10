//! 오케스트레이터 타입 정의
//!
//! Multi-agent 워크플로우에서 에이전트 간 관계와 실행 패턴을 정의한다.

use serde::{Deserialize, Serialize};

use crate::state_machine::agent_executor::AgentConfig;
use crate::state_machine::stage::HarnessStage;

/// 에이전트 정의 — Orchestrator에 전달되는 에이전트 스펙
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentDefinition {
    pub id: String,
    pub name: String,
    pub provider: String,
    pub model: String,
    pub stages: Vec<HarnessStage>,
    pub tools: Vec<String>,
    pub config: AgentConfig,
}

/// 에이전트 간 연결 — 결과 흐름 정의
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentConnection {
    /// 출력 에이전트 ID
    pub from: String,
    /// 입력 에이전트 ID
    pub to: String,
    /// 전달되는 아티팩트 이름 (예: "research", "output_stream")
    #[serde(default)]
    pub artifact: String,
}

/// 오케스트레이션 패턴
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum OrchestrationPattern {
    /// 순서대로 실행 (기본)
    #[default]
    Sequential,
    /// 파이프라인 — 앞 에이전트 출력 → 다음 에이전트 입력
    Pipeline,
    /// Supervisor 패턴 — lead 에이전트가 다른 에이전트를 조율
    #[serde(rename = "supervisor")]
    Supervisor { lead: String },
    /// 병렬 실행 — 모든 에이전트 동시 실행 후 집계
    Parallel,
}
