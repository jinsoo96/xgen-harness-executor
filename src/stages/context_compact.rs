use anyhow::Result;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::llm::provider::LlmProvider;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// ContextCompact 단계: 컨텍스트 버짓 체크 + 자동 압축
/// LLMCall 직전에 실행하여 토큰 한도 초과 방지
///
/// 3단계 압축 전략:
/// 1. History Snip — 오래된 대화 제거 (최근 4개 유지)
/// 2. RAG 축소 — 인덱스만 남기고 상세 제거
/// 3. LLM 요약 — 전체 대화를 3~5문장으로 (예산 심각 초과 시)
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    provider: &dyn LlmProvider,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!(
        messages = context.messages.len(),
        "ContextCompact: checking context budget"
    );

    let provider_name = &config.provider_name;
    let mut cwm = crate::context::window::ContextWindowManager::new(provider_name);
    let mut rag = context.rag_context.clone();

    let compacted = cwm.check_and_compact(
        &context.system_prompt,
        &mut context.messages,
        &mut rag,
    );

    if compacted {
        context.rag_context = rag;
        info!("ContextCompact: stage 1-2 compression applied");
    }

    // 3단계: 예산 심각 초과 시 LLM 요약
    if cwm.needs_llm_summary() {
        info!("ContextCompact: budget still exceeded, attempting LLM summary");

        let summary = summarize_with_llm(provider, &context.system_prompt, &context.messages, config).await;
        match summary {
            Ok(text) => {
                let recent: Vec<serde_json::Value> = if context.messages.len() > 2 {
                    context.messages[context.messages.len() - 2..].to_vec()
                } else {
                    context.messages.clone()
                };
                context.messages = vec![serde_json::json!({
                    "role": "user",
                    "content": format!("[이전 대화 요약]\n{}", text),
                })];
                context.messages.extend(recent);
                cwm.on_summary_success();
                info!("ContextCompact: LLM summary applied");
            }
            Err(e) => {
                cwm.on_summary_failure();
                tracing::warn!("ContextCompact: LLM summary failed: {}", e);
            }
        }
    }

    let _ = event_tx.send(SseEvent {
        event: "debug_log".to_string(),
        data: serde_json::json!({
            "message": format!("ContextCompact: messages={} compacted={}",
                context.messages.len(), compacted),
        }),
        id: None,
    });

    Ok(StageResult {
        stage: HarnessStage::ContextCompact,
        output: serde_json::json!({
            "messages_after": context.messages.len(),
            "compacted": compacted,
        }),
        score: None,
        error: None,
    })
}

async fn summarize_with_llm(
    provider: &dyn LlmProvider,
    _system_prompt: &str,
    messages: &[serde_json::Value],
    config: &AgentConfig,
) -> Result<String> {
    use crate::llm::provider::{ChatMessage, ChatRequest, MessageContent};

    let conversation_text: String = messages
        .iter()
        .filter_map(|m| {
            let role = m["role"].as_str().unwrap_or("unknown");
            let content = m["content"].as_str().unwrap_or("");
            if content.is_empty() { None } else { Some(format!("[{}]: {}", role, content)) }
        })
        .collect::<Vec<_>>()
        .join("\n\n");

    if conversation_text.len() < 2000 {
        return Err(anyhow::anyhow!("Conversation too short to summarize"));
    }

    let slice = if conversation_text.len() > 20_000 {
        &conversation_text[conversation_text.len() - 20_000..]
    } else {
        &conversation_text
    };

    let prompt = format!(
        "아래 대화를 분석하여 다음 9개 섹션으로 구조화하세요.\n\
         각 섹션은 해당 내용이 있을 때만 포함하세요.\n\n\
         1. **원래 요청**: 사용자가 처음에 요청한 것 (1~2문장)\n\
         2. **기술 개념**: 대화에서 다룬 핵심 기술 개념/용어\n\
         3. **파일/코드**: 언급된 파일 경로, 코드 조각, 데이터 구조\n\
         4. **에러/수정**: 발생한 에러와 적용된 수정사항\n\
         5. **문제 해결 과정**: 시도한 접근법과 결과\n\
         6. **사용자 메시지**: 사용자가 보낸 모든 메시지 요약\n\
         7. **미완료 작업**: 아직 끝나지 않은 작업 목록\n\
         8. **현재 진행**: 지금 하고 있는 작업 상태\n\
         9. **다음 단계**: 다음에 해야 할 작업 (원문 인용 포함)\n\n\
         CRITICAL: 텍스트만 출력하세요. 도구를 호출하지 마세요.\n\n대화:\n{}",
        slice
    );

    let response = provider.chat(ChatRequest {
        model: config.model.clone(),
        messages: vec![ChatMessage {
            role: "user".to_string(),
            content: MessageContent::Text(prompt),
            tool_calls: None,
            tool_call_id: None,
        }],
        system: Some("You are a structured conversation summarizer. Output ONLY the structured summary sections. Do NOT call any tools.".to_string()),
        temperature: 0.1,
        max_tokens: 2048,
        tools: None,
    }).await?;

    Ok(response.content)
}
