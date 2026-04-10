//! harness-v1 워크플로우 정의 스키마
//!
//! 기존 xgen-workflow의 노드/엣지 JSON 포맷과 별도로,
//! 하네스 실행기 전용 워크플로우 정의.

use serde::{Deserialize, Serialize};

use crate::state_machine::agent_executor::AgentConfig;
use crate::state_machine::orchestrator::{AgentConnection, AgentDefinition, OrchestrationPattern};
use crate::state_machine::stage::HarnessStage;

/// harness-v1 워크플로우 정의
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HarnessWorkflow {
    /// 포맷 버전 (반드시 "harness-v1")
    pub version: String,
    /// 워크플로우 ID
    #[serde(default)]
    pub id: String,
    /// 워크플로우 이름
    #[serde(default)]
    pub name: String,
    /// 오케스트레이션 패턴
    #[serde(default = "default_orchestration")]
    pub orchestration: OrchestrationPattern,
    /// 에이전트 정의 목록
    pub agents: Vec<HarnessAgentDef>,
    /// 에이전트 간 연결
    #[serde(default)]
    pub connections: Vec<AgentConnection>,
    /// 메타데이터
    #[serde(default)]
    pub metadata: serde_json::Value,
}

fn default_orchestration() -> OrchestrationPattern {
    OrchestrationPattern::Sequential
}

/// harness-v1 에이전트 정의
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HarnessAgentDef {
    pub id: String,
    pub name: String,
    #[serde(default = "default_provider")]
    pub provider: String,
    #[serde(default = "default_model")]
    pub model: String,
    /// 하네스 단계 (체크리스트) — 프리셋 문자열 또는 단계 배열
    #[serde(default)]
    pub stages: StagesConfig,
    /// MCP 도구 URI 목록
    #[serde(default)]
    pub tools: Vec<String>,
    /// 에이전트 설정
    #[serde(default)]
    pub config: AgentConfigOverride,
}

fn default_provider() -> String { "anthropic".to_string() }
fn default_model() -> String { "claude-sonnet-4-6".to_string() }

/// 단계 설정 — 프리셋 이름 또는 명시적 단계 배열
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum StagesConfig {
    /// 프리셋 이름: "minimal", "claude_code", "anthropic", "full"
    Preset(String),
    /// 명시적 단계 배열
    Custom(Vec<HarnessStage>),
}

impl Default for StagesConfig {
    fn default() -> Self {
        StagesConfig::Preset("minimal".to_string())
    }
}

impl StagesConfig {
    pub fn resolve(&self) -> Vec<HarnessStage> {
        match self {
            StagesConfig::Preset(name) => HarnessStage::preset(name),
            StagesConfig::Custom(stages) => stages.clone(),
        }
    }
}

/// 에이전트 설정 오버라이드 (모든 필드 optional)
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AgentConfigOverride {
    pub system_prompt: Option<String>,
    pub temperature: Option<f64>,
    pub max_tokens: Option<u32>,
    pub max_retries: Option<u32>,
    pub eval_threshold: Option<f64>,
    pub context_budget: Option<u64>,
    pub modules: Option<Vec<String>>,
}

impl HarnessWorkflow {
    /// 유효성 검증
    pub fn validate(&self) -> Result<(), Vec<String>> {
        let mut errors = Vec::new();

        if self.version != "harness-v1" {
            errors.push(format!("Invalid version: '{}', expected 'harness-v1'", self.version));
        }

        if self.agents.is_empty() {
            errors.push("At least one agent is required".to_string());
        }

        // 에이전트 ID 중복 검사
        let mut seen_ids = std::collections::HashSet::new();
        for agent in &self.agents {
            if !seen_ids.insert(&agent.id) {
                errors.push(format!("Duplicate agent ID: '{}'", agent.id));
            }
        }

        // 연결의 from/to 에이전트가 존재하는지 검증
        let agent_ids: std::collections::HashSet<&str> =
            self.agents.iter().map(|a| a.id.as_str()).collect();
        for conn in &self.connections {
            if !agent_ids.contains(conn.from.as_str()) {
                errors.push(format!("Connection 'from' agent not found: '{}'", conn.from));
            }
            if !agent_ids.contains(conn.to.as_str()) {
                errors.push(format!("Connection 'to' agent not found: '{}'", conn.to));
            }
        }

        // Supervisor 패턴이면 lead 에이전트 존재 검증
        if let OrchestrationPattern::Supervisor { ref lead } = self.orchestration {
            if !agent_ids.contains(lead.as_str()) {
                errors.push(format!("Supervisor lead agent not found: '{}'", lead));
            }
        }

        if errors.is_empty() { Ok(()) } else { Err(errors) }
    }

    /// AgentDefinition 목록으로 변환 (Orchestrator에 전달)
    pub fn to_agent_definitions(&self) -> Vec<AgentDefinition> {
        self.agents
            .iter()
            .map(|a| {
                let stages = a.stages.resolve();
                let config_override = &a.config;

                AgentDefinition {
                    id: a.id.clone(),
                    name: a.name.clone(),
                    provider: a.provider.clone(),
                    model: a.model.clone(),
                    stages,
                    tools: a.tools.clone(),
                    config: AgentConfig {
                        provider_name: a.provider.clone(),
                        model: a.model.clone(),
                        system_prompt: config_override.system_prompt.clone().unwrap_or_default(),
                        temperature: config_override.temperature.unwrap_or(0.7),
                        max_tokens: config_override.max_tokens.unwrap_or(8192),
                        max_retries: config_override.max_retries.unwrap_or(3),
                        eval_threshold: config_override.eval_threshold.unwrap_or(0.7),
                        context_budget: config_override.context_budget.unwrap_or(200_000),
                        tools: a.tools.clone(),
                        modules: config_override.modules.clone().unwrap_or_default(),
                    },
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_harness_v1() {
        let json = r#"{
            "version": "harness-v1",
            "orchestration": "sequential",
            "agents": [
                {
                    "id": "agent-1",
                    "name": "Research",
                    "stages": "anthropic",
                    "tools": ["mcp://docs/search"]
                },
                {
                    "id": "agent-2",
                    "name": "Writer",
                    "stages": ["init", "execute", "complete"],
                    "config": {
                        "temperature": 0.9,
                        "max_retries": 5
                    }
                }
            ],
            "connections": [
                {"from": "agent-1", "to": "agent-2", "artifact": "research"}
            ]
        }"#;

        let workflow: HarnessWorkflow = serde_json::from_str(json).unwrap();
        assert_eq!(workflow.version, "harness-v1");
        assert_eq!(workflow.agents.len(), 2);

        // Preset stages
        let stages1 = workflow.agents[0].stages.resolve();
        assert!(stages1.contains(&HarnessStage::Plan));
        assert!(stages1.contains(&HarnessStage::Validate));

        // Custom stages
        let stages2 = workflow.agents[1].stages.resolve();
        assert_eq!(stages2.len(), 3);

        // Config override
        assert_eq!(workflow.agents[1].config.temperature, Some(0.9));
        assert_eq!(workflow.agents[1].config.max_retries, Some(5));

        // Validation
        assert!(workflow.validate().is_ok());

        // AgentDefinition 변환
        let defs = workflow.to_agent_definitions();
        assert_eq!(defs.len(), 2);
        assert_eq!(defs[1].config.temperature, 0.9);
    }

    #[test]
    fn test_validation_errors() {
        let workflow = HarnessWorkflow {
            version: "wrong".to_string(),
            id: String::new(),
            name: String::new(),
            orchestration: OrchestrationPattern::Supervisor { lead: "nonexistent".to_string() },
            agents: vec![],
            connections: vec![],
            metadata: serde_json::Value::Null,
        };

        let errors = workflow.validate().unwrap_err();
        assert!(errors.iter().any(|e| e.contains("Invalid version")));
        assert!(errors.iter().any(|e| e.contains("At least one agent")));
        assert!(errors.iter().any(|e| e.contains("lead agent not found")));
    }
}
