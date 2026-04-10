use anyhow::Result;
use tokio::sync::mpsc;
use tracing::{info, warn};

use crate::events::SseEvent;
use crate::tools::orchestration::ToolOrchestrator;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// ToolExecute 단계: MCP 도구 실행 (LLMCall 후, 다시 LLMCall로 복귀)
///
/// LLMCall에서 tool_calls가 생성되면 이 단계가 실행됨.
/// 완료 후 상태 머신이 LLMCall로 다시 점프 (루프 구조).
///
/// 실행 전략:
/// - Read 도구 (search/read/get): 병렬 실행
/// - Write 도구 (execute/create/delete): 직렬 실행
pub async fn execute(
    _config: &AgentConfig,
    context: &mut ExecutionContext,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    // 마지막 assistant 메시지에서 tool_calls 추출
    let tool_calls_json = context
        .messages
        .iter()
        .rev()
        .find(|m| m["role"] == "assistant" && !m["tool_calls"].is_null())
        .and_then(|m| m["tool_calls"].as_array())
        .cloned()
        .unwrap_or_default();

    if tool_calls_json.is_empty() {
        // tool_calls가 없으면 패스스루 (ContextCompact 없는 파이프라인에서 발생 가능)
        return Ok(StageResult {
            stage: HarnessStage::ToolExecute,
            output: serde_json::json!({"tools_executed": 0}),
            score: None,
            error: None,
        });
    }

    let tool_calls: Vec<crate::llm::provider::ToolCall> = tool_calls_json
        .iter()
        .filter_map(|tc| {
            let id = tc["id"].as_str()?.to_string();
            let name = tc["function"]["name"].as_str()
                .or_else(|| tc["name"].as_str())?.to_string();
            let input: serde_json::Value = tc["function"]["arguments"]
                .as_str()
                .and_then(|s| serde_json::from_str(s).ok())
                .unwrap_or_else(|| tc["input"].clone());
            Some(crate::llm::provider::ToolCall { id, name, input })
        })
        .collect();

    info!(count = tool_calls.len(), "ToolExecute: executing MCP tools");

    let tools_executed = tool_calls.len();

    if let Some(ref mcp_mgr) = context.mcp_manager {
        let results = ToolOrchestrator::execute_tool_calls(mcp_mgr, &tool_calls).await;

        for result in results {
            let _ = event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!("ToolExecute: {} error={}", result.tool_name, result.is_error),
                }),
                id: None,
            });

            let _ = event_tx.send(SseEvent {
                event: "tool_result".to_string(),
                data: serde_json::json!({
                    "tool_id": result.tool_call_id,
                    "tool_name": result.tool_name,
                    "is_error": result.is_error,
                    "content_length": result.content.len(),
                }),
                id: None,
            });

            context.messages.push(serde_json::json!({
                "role": "tool",
                "tool_call_id": result.tool_call_id,
                "content": result.content,
            }));
        }
    } else {
        // MCP 매니저 없음 — stub 결과
        for tc in &tool_calls {
            warn!(tool = %tc.name, "ToolExecute: no MCP manager, returning stub");
            context.messages.push(serde_json::json!({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": format!(
                    "Tool '{}' called but no MCP server connected. Configure tools in agent config.",
                    tc.name
                ),
            }));
        }
    }

    Ok(StageResult {
        stage: HarnessStage::ToolExecute,
        output: serde_json::json!({
            "tools_executed": tools_executed,
            "has_more": true, // 상태 머신이 LLMCall로 다시 점프
        }),
        score: None,
        error: None,
    })
}
