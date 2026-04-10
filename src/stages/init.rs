use anyhow::Result;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::context::memory::MemoryPrefetcher;
use crate::context::sections::PromptSectionManager;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// Init 단계: 컨텍스트 로드 + 메모리 프리페치 + RAG 인덱스 빌드
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    _event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!("Init stage: loading context");

    // 1. 시스템 프롬프트 설정
    {
        let base_prompt = if config.system_prompt.is_empty() {
            default_system_prompt()
        } else {
            config.system_prompt.clone()
        };

        let mut section_mgr = PromptSectionManager::new();
        section_mgr.add_role(&base_prompt);
        context.system_prompt = section_mgr.build(None);
    }

    // 2. 입력 메시지 구성 (텍스트 + 이미지 블록)
    let user_text = context
        .input
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let image_blocks = context
        .input
        .get("image_blocks")
        .and_then(|v| v.as_array())
        .filter(|arr| !arr.is_empty());

    if let Some(blocks) = image_blocks {
        // 이미지 첨부 있음 → MessageContent::Blocks (Anthropic Vision 형식)
        let mut content: Vec<serde_json::Value> = vec![
            serde_json::json!({"type": "text", "text": user_text}),
        ];
        content.extend(blocks.iter().cloned());
        context.messages.push(serde_json::json!({
            "role": "user",
            "content": content,
        }));
        info!("Init stage: built multimodal message ({} image blocks)", blocks.len());
    } else if !user_text.is_empty() {
        context.messages.push(serde_json::json!({
            "role": "user",
            "content": user_text,
        }));
    }

    // 3. 메모리 프리페치: input["previous_results"]에서 이전 실행 결과 로드 후 키워드 매칭
    {
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

        if !previous.is_empty() {
            let query_text = context
                .input
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("");

            let mut prefetcher = MemoryPrefetcher::new();
            let memory_ctx = prefetcher.prefetch(query_text, &previous, &[]);

            if !memory_ctx.is_empty() {
                let mut section_mgr = PromptSectionManager::new();
                section_mgr.add_role(&context.system_prompt);
                section_mgr.add("memory_context", Some(memory_ctx.clone()), false, 6, true);
                context.system_prompt = section_mgr.build(None);
                info!(
                    memories = previous.len(),
                    injected_chars = memory_ctx.len(),
                    "Memory prefetch injected into system prompt"
                );
            }
        }
    }

    // 4. RAG 인덱스 빌드 (도구로 제공, 여기서는 컨텍스트에 인덱스만 추가)
    // Phase 1에서 MCP 도구로 구현

    Ok(StageResult {
        stage: HarnessStage::Init,
        output: serde_json::json!({
            "status": "initialized",
            "message_count": context.messages.len(),
        }),
        score: None,
        error: None,
    })
}

fn default_system_prompt() -> String {
    "You are a helpful AI assistant. Answer the user's questions accurately and concisely."
        .to_string()
}
