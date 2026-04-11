use anyhow::Result;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::llm::provider::{ChatMessage, ChatRequest, LlmProvider, MessageContent};
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// 스프린트 계약 프롬프트 (Python prompts.py에서 포팅)
const SPRINT_CONTRACT_PROMPT: &str = r#"Before executing, declare your plan:

1. **Goal**: What will you accomplish this turn? (1 sentence)
2. **Information Needed**: What information do you need to gather?
3. **Search Strategy**: Which tools will you use and in what order?
4. **Completion Criteria**: How will you know you're done?

Be specific and actionable. This contract guides your execution."#;

/// Plan 단계: 스프린트 계약 + 도구 디스커버리
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    provider: &dyn LlmProvider,
    _event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!("Plan stage: generating sprint contract");

    // 스프린트 계약 생성을 위한 LLM 호출
    let plan_system = format!(
        "{}\n\n{}",
        context.system_prompt, SPRINT_CONTRACT_PROMPT
    );

    // Plan은 도구 없이 호출 → tool/assistant(tool_calls) 메시지 제거
    let plan_messages: Vec<ChatMessage> = context.messages.iter()
        .filter(|m| {
            let role = m["role"].as_str().unwrap_or("");
            role != "tool" && !(role == "assistant" && !m["tool_calls"].is_null())
        })
        .map(|m| ChatMessage {
            role: m["role"].as_str().unwrap_or("user").to_string(),
            content: MessageContent::Text(
                m["content"].as_str().unwrap_or("").to_string()
            ),
            tool_calls: None,
            tool_call_id: None,
        })
        .collect();

    let request = ChatRequest {
        model: config.model.clone(),
        messages: plan_messages,
        system: Some(plan_system),
        temperature: config.temperature,
        max_tokens: 1024,
        tools: None,
    };

    let response = provider.chat(request).await?;

    // 스프린트 계약 내용을 SSE 이벤트로 전송 (프론트엔드 실행 로그에 표시)
    let _ = _event_tx.send(SseEvent {
        event: "plan_contract".to_string(),
        data: serde_json::json!({
            "contract": response.content,
            "usage": response.usage,
        }),
        id: None,
    });

    // 계획을 컨텍스트에 저장 (Execute 단계에서 참조)
    context.system_prompt = format!(
        "{}\n\n## Current Sprint Plan\n{}",
        context.system_prompt, response.content
    );

    // 도구 디스커버리는 ToolDiscovery 단계로 분리됨
    // ToolDiscovery가 파이프라인에 없는 경우 Plan에서 폴백 처리
    if context.tool_registry.is_some()
        && !context.system_prompt.contains("## 사용 가능한 도구")
    {
        if let Some(registry) = &context.tool_registry {
            let index = registry.get_tool_index();
            if !index.is_empty() {
                let tool_lines: Vec<String> = index
                    .iter()
                    .map(|cat| format!("- **{}**: {} ({}개)", cat.name, cat.description, cat.tools.len()))
                    .collect();
                context.system_prompt.push_str(&format!("\n\n## 사용 가능한 도구\n{}", tool_lines.join("\n")));
                info!("Plan: tool discovery fallback (ToolDiscovery stage not in pipeline)");
            }
        }
    }

    Ok(StageResult {
        stage: HarnessStage::Plan,
        output: serde_json::json!({
            "sprint_contract": response.content,
        }),
        score: None,
        error: None,
    })
}
