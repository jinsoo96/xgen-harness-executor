use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use tracing::info;

use crate::llm::provider::ToolDefinition;
use crate::mcp::client::McpToolInfo;

/// 에이전트 역할 — 역할별 도구 접근 제어
/// (Python tool_discovery.py에서 포팅)
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentRole {
    /// 읽기 전용 탐색 에이전트 (검색 도구만)
    Explore,
    /// 생성 에이전트 (모든 도구)
    Generator,
    /// 평가 에이전트 (읽기 + 평가 도구)
    Evaluator,
    /// 커스텀 (지정된 도구만)
    Custom(Vec<String>),
}

/// 도구 카테고리
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCategory {
    pub name: String,
    pub description: String,
    pub tools: Vec<String>,
}

/// 도구 레지스트리 — MCP 서버에서 발견된 도구 관리
pub struct ToolRegistry {
    /// 전체 도구 목록 (서버 이름 → 도구 목록)
    tools_by_server: HashMap<String, Vec<McpToolInfo>>,
    /// 도구 이름 → LLM ToolDefinition 변환 캐시
    definition_cache: HashMap<String, ToolDefinition>,
    /// 카테고리별 분류
    categories: Vec<ToolCategory>,
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self {
            tools_by_server: HashMap::new(),
            definition_cache: HashMap::new(),
            categories: Vec::new(),
        }
    }

    /// MCP 서버의 도구 목록 등록
    pub fn register_server_tools(&mut self, server_name: &str, tools: Vec<McpToolInfo>) {
        for tool in &tools {
            let def = mcp_to_llm_tool(tool);
            self.definition_cache.insert(tool.name.clone(), def);
        }
        info!(
            server = server_name,
            count = tools.len(),
            "Registered tools from MCP server"
        );
        self.tools_by_server
            .insert(server_name.to_string(), tools);
    }

    /// 역할에 따라 접근 가능한 도구 필터링
    /// (Python tool_discovery.py 포팅)
    pub fn get_tools_for_role(&self, role: &AgentRole) -> Vec<&ToolDefinition> {
        match role {
            AgentRole::Explore => {
                // 읽기 전용: search, read, list, get, query 패턴만
                self.definition_cache
                    .values()
                    .filter(|t| is_read_only_tool(&t.name))
                    .collect()
            }
            AgentRole::Generator => {
                // 모든 도구
                self.definition_cache.values().collect()
            }
            AgentRole::Evaluator => {
                // 읽기 + evaluate 패턴
                self.definition_cache
                    .values()
                    .filter(|t| is_read_only_tool(&t.name) || t.name.contains("evaluate"))
                    .collect()
            }
            AgentRole::Custom(allowed) => self
                .definition_cache
                .values()
                .filter(|t| allowed.contains(&t.name))
                .collect(),
        }
    }

    /// 지연 스키마 로딩: 카테고리 인덱스만 반환
    /// (Progressive Disclosure — Claude Code 패턴)
    pub fn get_tool_index(&self) -> Vec<ToolCategory> {
        // 자동 카테고리화
        let mut categories: HashMap<String, Vec<String>> = HashMap::new();

        for tool_name in self.definition_cache.keys() {
            let category = categorize_tool(tool_name);
            categories
                .entry(category)
                .or_default()
                .push(tool_name.clone());
        }

        categories
            .into_iter()
            .map(|(name, tools)| ToolCategory {
                description: format!("{} tools ({})", name, tools.len()),
                name,
                tools,
            })
            .collect()
    }

    /// 특정 카테고리의 도구 스키마 반환 (on-demand)
    pub fn get_tools_by_category(&self, category: &str) -> Vec<&ToolDefinition> {
        self.definition_cache
            .values()
            .filter(|t| categorize_tool(&t.name) == category)
            .collect()
    }

    /// 전체 도구 수
    pub fn total_tools(&self) -> usize {
        self.definition_cache.len()
    }
}

/// MCP 도구 정보 → LLM ToolDefinition 변환
fn mcp_to_llm_tool(mcp_tool: &McpToolInfo) -> ToolDefinition {
    ToolDefinition {
        name: mcp_tool.name.clone(),
        description: mcp_tool
            .description
            .clone()
            .unwrap_or_else(|| format!("Tool: {}", mcp_tool.name)),
        input_schema: mcp_tool.input_schema.clone(),
    }
}

/// 읽기 전용 도구 판별
fn is_read_only_tool(name: &str) -> bool {
    let read_prefixes = [
        "search", "read", "list", "get", "query", "fetch", "find", "check", "view", "browse",
    ];
    let lower = name.to_lowercase();
    read_prefixes.iter().any(|p| lower.contains(p))
}

/// 도구 이름에서 카테고리 추론
fn categorize_tool(name: &str) -> String {
    let lower = name.to_lowercase();
    if lower.contains("search") || lower.contains("document") || lower.contains("rag") {
        "document_search".to_string()
    } else if lower.contains("sql") || lower.contains("data") || lower.contains("stats") {
        "data_analysis".to_string()
    } else if lower.contains("python") || lower.contains("code") || lower.contains("execute") {
        "code_execution".to_string()
    } else if lower.contains("web") || lower.contains("api") || lower.contains("http") {
        "external".to_string()
    } else {
        "general".to_string()
    }
}
