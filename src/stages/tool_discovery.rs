use anyhow::Result;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// ToolDiscovery 단계: MCP 도구 탐색 + 시스템 프롬프트에 도구 인덱스 주입
/// Plan 단계와 분리 — 도구 목록을 먼저 파악한 뒤 Plan에서 참조
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!("ToolDiscovery: indexing available MCP tools");

    let (categories, total_tools) = if let Some(ref registry) = context.tool_registry {
        let index = registry.get_tool_index();
        let total = registry.total_tools();

        // 전체 도구 이름 목록 (디버그용)
        let all_tool_names: Vec<String> = registry
            .get_tools_for_role(&crate::tools::registry::AgentRole::Generator)
            .iter()
            .map(|t| t.name.clone())
            .collect();

        if !index.is_empty() {
            let tool_lines: Vec<String> = index
                .iter()
                .map(|cat| {
                    format!(
                        "- **{}**: {} ({}개 도구)",
                        cat.name, cat.description, cat.tools.len()
                    )
                })
                .collect();

            let tool_section = format!(
                "\n\n## 사용 가능한 도구\n{}",
                tool_lines.join("\n")
            );
            context.system_prompt.push_str(&tool_section);

            let _ = event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!("ToolDiscovery: {}개 카테고리, {}개 도구 주입 ({})",
                        index.len(), total, all_tool_names.join(", ")),
                }),
                id: None,
            });

            info!(categories = index.len(), total, "Tool index injected into system prompt");
            (index.len(), total)
        } else {
            let _ = event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!("ToolDiscovery: 카테고리 없음, 전체 도구 {}개 ({})",
                        total, all_tool_names.join(", ")),
                }),
                id: None,
            });
            (0, total)
        }
    } else {
        let _ = event_tx.send(SseEvent {
            event: "debug_log".to_string(),
            data: serde_json::json!({
                "message": format!("ToolDiscovery: MCP 연결 없음 (config.tools={}개)",
                    config.tools.len()),
            }),
            id: None,
        });
        (0, 0)
    };

    Ok(StageResult {
        stage: HarnessStage::ToolDiscovery,
        output: serde_json::json!({
            "categories": categories,
            "total_tools": total_tools,
        }),
        score: None,
        error: None,
    })
}
