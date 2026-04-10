use anyhow::Result;
use chrono::Utc;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::context::sections::PromptSectionManager;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// ContextBuild 단계: PromptSectionManager로 시스템 프롬프트 조립 + 입력 메시지 구성
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!("ContextBuild: assembling system prompt and input message");

    // 시스템 프롬프트가 아직 기본값(config)이면 조립
    if context.system_prompt.is_empty() || context.system_prompt == config.system_prompt {
        let base_prompt = if config.system_prompt.is_empty() {
            default_system_prompt()
        } else {
            config.system_prompt.clone()
        };
        let mut section_mgr = PromptSectionManager::new();
        section_mgr.add_role(&base_prompt);

        // 도구 사용 지침
        let tool_names: Vec<String> = context
            .input
            .get("tool_names")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        section_mgr.add_tool_guidelines(&tool_names);

        // 톤 & 스타일
        let style = context
            .input
            .get("tone_style")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        section_mgr.add_tone_style(style);

        // 출력 효율
        section_mgr.add_output_efficiency();

        // 환경 정보 (모델, 워크플로우, 날짜)
        let workflow_id = context
            .input
            .get("workflow_id")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");
        let date = Utc::now().format("%Y-%m-%d").to_string();
        section_mgr.add_environment_info(
            &config.model,
            workflow_id,
            &config.provider_name,
            &date,
        );

        context.system_prompt = section_mgr.build(None);
    }

    // 입력 메시지가 아직 없으면 구성 (Bootstrap/MemoryRead 이후 첫 실행)
    if context.messages.is_empty() {
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
            let mut content: Vec<serde_json::Value> = vec![
                serde_json::json!({"type": "text", "text": user_text}),
            ];
            content.extend(blocks.iter().cloned());
            context.messages.push(serde_json::json!({
                "role": "user",
                "content": content,
            }));
            info!("ContextBuild: multimodal message ({} image blocks)", blocks.len());
        } else if !user_text.is_empty() {
            context.messages.push(serde_json::json!({
                "role": "user",
                "content": user_text,
            }));
        }
    }

    let _ = event_tx.send(SseEvent {
        event: "debug_log".to_string(),
        data: serde_json::json!({
            "message": format!("ContextBuild: prompt={}chars messages={}",
                context.system_prompt.len(), context.messages.len()),
        }),
        id: None,
    });

    Ok(StageResult {
        stage: HarnessStage::ContextBuild,
        output: serde_json::json!({
            "prompt_chars": context.system_prompt.len(),
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
