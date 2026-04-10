use anyhow::Result;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::context::memory::MemoryPrefetcher;
use crate::context::sections::PromptSectionManager;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// MemoryRead 단계: 이전 실행 컨텍스트 프리페치 + 키워드 매칭
/// input["previous_results"]에서 관련 내용을 찾아 system_prompt에 주입
pub async fn execute(
    _config: &AgentConfig,
    context: &mut ExecutionContext,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!("MemoryRead: prefetching previous execution context");

    let previous: Vec<String> = context
        .input
        .get("previous_results")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    if previous.is_empty() {
        let _ = event_tx.send(SseEvent {
            event: "debug_log".to_string(),
            data: serde_json::json!({"message": "MemoryRead: no previous results"}),
            id: None,
        });
        return Ok(StageResult {
            stage: HarnessStage::MemoryRead,
            output: serde_json::json!({"injected": 0}),
            score: None,
            error: None,
        });
    }

    let query_text = context
        .input
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let mut prefetcher = MemoryPrefetcher::new();
    let memory_ctx = prefetcher.prefetch(query_text, &previous, &[]);

    let injected_chars = memory_ctx.len();

    if !memory_ctx.is_empty() {
        let mut section_mgr = PromptSectionManager::new();
        section_mgr.add_role(&context.system_prompt);
        section_mgr.add("memory_context", Some(memory_ctx.clone()), false, 6, true);
        context.system_prompt = section_mgr.build(None);

        info!(
            memories = previous.len(),
            injected_chars,
            "MemoryRead: injected into system prompt"
        );
    }

    let _ = event_tx.send(SseEvent {
        event: "debug_log".to_string(),
        data: serde_json::json!({
            "message": format!("MemoryRead: {} previous results → {} chars injected",
                previous.len(), injected_chars),
        }),
        id: None,
    });

    Ok(StageResult {
        stage: HarnessStage::MemoryRead,
        output: serde_json::json!({
            "previous_count": previous.len(),
            "injected_chars": injected_chars,
        }),
        score: None,
        error: None,
    })
}
