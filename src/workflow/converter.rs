//! 기존 xgen-workflow 포맷 → harness-v1 변환기
//!
//! 두 가지 입력 포맷을 모두 처리:
//! 1. React Flow 포맷: node.data.id / node.data.parameters (배열)
//! 2. 정규화 포맷: node.nodeId / node.parameters (dict)

use serde::Deserialize;
use tracing::info;

use crate::state_machine::orchestrator::{AgentConnection, OrchestrationPattern};
use crate::workflow::definition::*;

/// 내부 정규화된 노드 (변환 처리 후)
#[derive(Debug, Clone)]
pub struct LegacyNode {
    pub id: String,
    pub name: String,
    pub node_id: String,
    /// dict 형태 파라미터 (이미 정규화됨)
    pub parameters: serde_json::Value,
}

/// 내부 정규화된 엣지
#[derive(Debug, Clone)]
pub struct LegacyEdge {
    pub source: LegacyEdgeEnd,
    pub target: LegacyEdgeEnd,
}

#[derive(Debug, Clone)]
pub struct LegacyEdgeEnd {
    pub node_id: String,
    pub port_id: String,
}

/// 기존 워크플로우 전체 (정규화된 내부 표현)
#[derive(Debug, Clone)]
pub struct LegacyWorkflow {
    pub nodes: Vec<LegacyNode>,
    pub edges: Vec<LegacyEdge>,
}

impl<'de> Deserialize<'de> for LegacyWorkflow {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let raw = serde_json::Value::deserialize(deserializer)?;
        LegacyWorkflow::from_raw_value(&raw).map_err(serde::de::Error::custom)
    }
}

impl LegacyWorkflow {
    /// serde_json::Value 에서 파싱 — React Flow / 정규화 포맷 모두 처리
    pub fn from_raw_value(raw: &serde_json::Value) -> Result<Self, String> {
        let nodes_raw = raw["nodes"].as_array()
            .ok_or("workflow_data.nodes must be an array")?;

        let edges_raw = raw["edges"].as_array()
            .cloned()
            .unwrap_or_default();

        let nodes: Vec<LegacyNode> = nodes_raw
            .iter()
            .filter_map(|n| parse_node(n))
            .collect();

        let edges: Vec<LegacyEdge> = edges_raw
            .iter()
            .filter_map(|e| parse_edge(e))
            .collect();

        Ok(LegacyWorkflow { nodes, edges })
    }
}

/// 노드 파싱 — React Flow / 정규화 포맷 자동 감지
fn parse_node(n: &serde_json::Value) -> Option<LegacyNode> {
    let id = n["id"].as_str()?.to_string();

    // React Flow 포맷: data 래퍼 있음
    if let Some(data) = n.get("data").and_then(|d| d.as_object()) {
        let node_spec_id = data.get("id")
            .or_else(|| data.get("nodeId"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let name = data.get("nodeName")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        // parameters: 배열 → dict 변환
        let params = normalize_params(data.get("parameters"));

        return Some(LegacyNode { id, name, node_id: node_spec_id, parameters: params });
    }

    // 정규화 포맷: nodeId / parameters 직접
    let node_id = n.get("nodeId")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    if node_id.is_empty() {
        return None;
    }

    let name = n.get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let params = normalize_params(n.get("parameters"));

    Some(LegacyNode { id, name, node_id, parameters: params })
}

/// parameters: 배열([{id, value}]) 또는 dict({key: value}) 모두 처리
fn normalize_params(params_val: Option<&serde_json::Value>) -> serde_json::Value {
    match params_val {
        None => serde_json::Value::Object(Default::default()),
        Some(serde_json::Value::Array(arr)) => {
            let mut map = serde_json::Map::new();
            for p in arr {
                let key = p.get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim_start_matches('*');
                if key.is_empty() { continue; }
                let val = p.get("value").cloned().unwrap_or(serde_json::Value::Null);
                map.insert(key.to_string(), val);
            }
            // model 필드 정규화: openai_model / anthropic_model → model
            if !map.contains_key("model") || map["model"].as_str().map(|s| s.is_empty()).unwrap_or(true) {
                for alt in &["openai_model", "anthropic_model", "claude_model", "gemini_model"] {
                    if let Some(v) = map.get(*alt).cloned() {
                        if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                            map.insert("model".to_string(), v);
                            break;
                        }
                    }
                }
            }
            serde_json::Value::Object(map)
        }
        Some(serde_json::Value::Object(obj)) => {
            let mut map = obj.clone();
            // model 필드 정규화
            if !map.contains_key("model") || map["model"].as_str().map(|s| s.is_empty()).unwrap_or(true) {
                for alt in &["openai_model", "anthropic_model", "claude_model", "gemini_model"] {
                    if let Some(v) = map.get(*alt).cloned() {
                        if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                            map.insert("model".to_string(), v);
                            break;
                        }
                    }
                }
            }
            serde_json::Value::Object(map)
        }
        Some(other) => other.clone(),
    }
}

/// 엣지 파싱 — React Flow 플랫 / 중첩 포맷 자동 감지
fn parse_edge(e: &serde_json::Value) -> Option<LegacyEdge> {
    let source_val = e.get("source")?;
    let target_val = e.get("target")?;

    let (src_node_id, src_port_id) = extract_edge_end(source_val, e.get("sourceHandle"))?;
    let (tgt_node_id, tgt_port_id) = extract_edge_end(target_val, e.get("targetHandle"))?;

    Some(LegacyEdge {
        source: LegacyEdgeEnd { node_id: src_node_id, port_id: src_port_id },
        target: LegacyEdgeEnd { node_id: tgt_node_id, port_id: tgt_port_id },
    })
}

fn extract_edge_end(
    val: &serde_json::Value,
    handle: Option<&serde_json::Value>,
) -> Option<(String, String)> {
    match val {
        // React Flow: "source": "node-id-string"
        serde_json::Value::String(node_id) => {
            let port = handle.and_then(|h| h.as_str()).unwrap_or("").to_string();
            Some((node_id.clone(), port))
        }
        // 중첩: "source": {"nodeId": "...", "portId": "..."}
        serde_json::Value::Object(obj) => {
            let node_id = obj.get("nodeId")
                .and_then(|v| v.as_str())?
                .to_string();
            let port_id = obj.get("portId")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            Some((node_id, port_id))
        }
        _ => None,
    }
}

/// 기존 워크플로우 → harness-v1 변환
pub fn convert_legacy_to_harness(
    legacy: &LegacyWorkflow,
    workflow_id: &str,
    workflow_name: &str,
) -> Result<HarnessWorkflow, String> {
    // Agent 노드만 추출
    let agent_nodes: Vec<&LegacyNode> = legacy
        .nodes
        .iter()
        .filter(|n| is_agent_node(&n.node_id))
        .collect();

    // 비에이전트 노드 존재 여부 → Node MCP Bridge 자동 주입
    let has_non_agent_nodes = legacy.nodes.iter().any(|n| !is_agent_node(&n.node_id));

    // agent 노드가 없으면 자동 생성 (비에이전트만 있는 워크플로우 지원)
    let agent_nodes = if agent_nodes.is_empty() {
        // 기본 에이전트를 생성해서 비에이전트 노드를 도구로 활용
        vec![]
    } else {
        agent_nodes
    };

    let agents: Vec<HarnessAgentDef> = if agent_nodes.is_empty() {
        let mut tools = vec!["mcp://bridge/services".to_string()];
        if has_non_agent_nodes {
            tools.push("mcp://bridge/nodes".to_string());
        }
        vec![HarnessAgentDef {
            id: "auto-agent".to_string(),
            name: workflow_name.to_string(),
            provider: "anthropic".to_string(),
            model: "claude-sonnet-4-6".to_string(),
            stages: StagesConfig::Preset("minimal".to_string()),
            tools,
            config: AgentConfigOverride::default(),
        }]
    } else {
        agent_nodes
            .iter()
            .map(|node| {
                let mut agent = convert_agent_node(node);
                // 모든 에이전트에 서비스 도구(문서검색 등) 자동 연결
                agent.tools.push("mcp://bridge/services".to_string());
                // 비에이전트 노드 존재 시 Node MCP Bridge 자동 연결
                if has_non_agent_nodes {
                    agent.tools.push("mcp://bridge/nodes".to_string());
                }
                agent
            })
            .collect()
    };

    let agent_ids: std::collections::HashSet<&str> =
        agent_nodes.iter().map(|n| n.id.as_str()).collect();

    let connections: Vec<AgentConnection> = legacy
        .edges
        .iter()
        .filter(|e| {
            agent_ids.contains(e.source.node_id.as_str())
                && agent_ids.contains(e.target.node_id.as_str())
        })
        .map(|e| AgentConnection {
            from: e.source.node_id.clone(),
            to: e.target.node_id.clone(),
            artifact: format!("output_{}", e.source.port_id),
        })
        .collect();

    let pattern = infer_orchestration_pattern(legacy, &agent_nodes);

    let workflow = HarnessWorkflow {
        version: "harness-v1".to_string(),
        id: workflow_id.to_string(),
        name: workflow_name.to_string(),
        orchestration: pattern,
        agents,
        connections,
        metadata: serde_json::json!({
            "converted_from": "legacy",
            "original_node_count": legacy.nodes.len(),
            "original_edge_count": legacy.edges.len(),
        }),
    };

    info!(
        agents = workflow.agents.len(),
        connections = workflow.connections.len(),
        "Converted legacy workflow to harness-v1"
    );

    Ok(workflow)
}

fn is_agent_node(node_id: &str) -> bool {
    node_id.starts_with("agents/") || node_id == "agents" || node_id.contains("agent_xgen")
}

fn convert_agent_node(node: &LegacyNode) -> HarnessAgentDef {
    let params = &node.parameters;

    let harness_pipeline = params
        .get("harness_pipeline")
        .and_then(|v| v.as_str())
        .unwrap_or("none");

    let stages = match harness_pipeline {
        "none" | "" => StagesConfig::Preset("minimal".to_string()),
        other => StagesConfig::Preset(other.to_string()),
    };

    let provider = params
        .get("provider")
        .and_then(|v| v.as_str())
        .unwrap_or("anthropic")
        .to_string();

    // model: model → openai_model → anthropic_model → 기본값
    let model = params.get("model")
        .or_else(|| params.get("openai_model"))
        .or_else(|| params.get("anthropic_model"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("claude-sonnet-4-6")
        .to_string();

    HarnessAgentDef {
        id: node.id.clone(),
        name: if node.name.is_empty() {
            format!("Agent {}", &node.id[..8.min(node.id.len())])
        } else {
            node.name.clone()
        },
        provider,
        model,
        stages,
        tools: vec![],
        config: AgentConfigOverride {
            system_prompt: params.get("system_prompt").and_then(|v| v.as_str()).map(String::from),
            temperature: params.get("temperature").and_then(|v| v.as_f64()),
            max_tokens: params.get("max_tokens").and_then(|v| v.as_u64()).map(|v| v as u32),
            max_retries: None,
            eval_threshold: None,
            context_budget: None,
            modules: None,
        },
    }
}

fn infer_orchestration_pattern(
    legacy: &LegacyWorkflow,
    agent_nodes: &[&LegacyNode],
) -> OrchestrationPattern {
    if agent_nodes.len() == 1 {
        return OrchestrationPattern::Sequential;
    }

    let has_router = legacy.nodes.iter().any(|n| n.node_id.contains("router"));
    if has_router {
        return OrchestrationPattern::Supervisor {
            lead: agent_nodes[0].id.clone(),
        };
    }

    let has_evaluator = legacy.nodes.iter().any(|n| {
        n.node_id.contains("evaluator")
            || n.parameters
                .get("harness_pipeline")
                .and_then(|v| v.as_str())
                .map(|s| s == "anthropic" || s == "full")
                .unwrap_or(false)
    });
    if has_evaluator {
        return OrchestrationPattern::Pipeline;
    }

    OrchestrationPattern::Sequential
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_node(id: &str, node_id: &str, params: serde_json::Value) -> LegacyNode {
        LegacyNode {
            id: id.to_string(),
            name: id.to_string(),
            node_id: node_id.to_string(),
            parameters: params,
        }
    }

    #[test]
    fn test_convert_simple_workflow() {
        let legacy = LegacyWorkflow {
            nodes: vec![
                make_node("node-1", "agents/xgen", serde_json::json!({
                    "provider": "anthropic", "model": "claude-sonnet-4-6",
                    "harness_pipeline": "claude_code", "temperature": 0.3
                })),
                make_node("node-2", "agents/xgen", serde_json::json!({
                    "provider": "openai", "model": "gpt-4.1",
                    "harness_pipeline": "anthropic"
                })),
            ],
            edges: vec![LegacyEdge {
                source: LegacyEdgeEnd { node_id: "node-1".to_string(), port_id: "stream".to_string() },
                target: LegacyEdgeEnd { node_id: "node-2".to_string(), port_id: "text".to_string() },
            }],
        };

        let workflow = convert_legacy_to_harness(&legacy, "wf-1", "Test").unwrap();
        assert_eq!(workflow.version, "harness-v1");
        assert_eq!(workflow.agents.len(), 2);
        assert_eq!(workflow.connections.len(), 1);
        assert_eq!(workflow.agents[0].provider, "anthropic");
        assert_eq!(workflow.agents[1].provider, "openai");
        assert!(workflow.validate().is_ok());
    }

    #[test]
    fn test_react_flow_format() {
        // React Flow 포맷 직접 파싱 테스트
        let raw = serde_json::json!({
            "nodes": [
                {
                    "id": "n_agent",
                    "data": {
                        "id": "agents/xgen",
                        "nodeName": "Agent",
                        "categoryId": "xgen",
                        "functionId": "agents",
                        "parameters": [
                            {"id": "provider", "value": "openai"},
                            {"id": "openai_model", "value": "gpt-4o-mini"},
                            {"id": "harness_pipeline", "value": "anthropic"},
                            {"id": "system_prompt", "value": "Test"}
                        ]
                    }
                }
            ],
            "edges": []
        });
        let legacy = LegacyWorkflow::from_raw_value(&raw).unwrap();
        assert_eq!(legacy.nodes.len(), 1);
        assert_eq!(legacy.nodes[0].node_id, "agents/xgen");
        assert_eq!(legacy.nodes[0].parameters["model"].as_str().unwrap(), "gpt-4o-mini");
        assert_eq!(legacy.nodes[0].parameters["provider"].as_str().unwrap(), "openai");

        let workflow = convert_legacy_to_harness(&legacy, "test", "Test").unwrap();
        assert_eq!(workflow.agents[0].model, "gpt-4o-mini");
        assert_eq!(workflow.agents[0].provider, "openai");
    }

    #[test]
    fn test_nested_edge_format() {
        // 기존 중첩 엣지 포맷 파싱
        let raw = serde_json::json!({
            "nodes": [
                {"id": "n1", "data": {"id": "agents/xgen", "nodeName": "A", "parameters": []}},
                {"id": "n2", "data": {"id": "agents/xgen", "nodeName": "B", "parameters": []}}
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": {"nodeId": "n1", "portId": "stream"},
                    "target": {"nodeId": "n2", "portId": "text"}
                }
            ]
        });
        let legacy = LegacyWorkflow::from_raw_value(&raw).unwrap();
        assert_eq!(legacy.edges.len(), 1);
        assert_eq!(legacy.edges[0].source.node_id, "n1");

        let workflow = convert_legacy_to_harness(&legacy, "test", "Test").unwrap();
        assert_eq!(workflow.connections.len(), 1);
    }

    #[test]
    fn test_infer_pipeline() {
        let legacy = LegacyWorkflow {
            nodes: vec![
                make_node("n1", "agents/xgen", serde_json::json!({"harness_pipeline": "full"})),
            ],
            edges: vec![],
        };
        let agents: Vec<&LegacyNode> = legacy.nodes.iter().collect();
        let pattern = infer_orchestration_pattern(&legacy, &agents);
        matches!(pattern, OrchestrationPattern::Sequential);
    }
}
