use anyhow::Result;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// Bootstrap 단계: API 키 확인, 기본 설정 초기화
/// 실행 전 사전 조건 검증 — 실패 시 빠른 에러 반환
pub async fn execute(
    config: &AgentConfig,
    _context: &mut ExecutionContext,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!(
        provider = %config.provider_name,
        model = %config.model,
        "Bootstrap: validating config"
    );

    // provider/model 유효성 체크
    let supported_providers = ["openai", "anthropic"];
    if !supported_providers.contains(&config.provider_name.as_str()) {
        return Err(anyhow::anyhow!(
            "Unsupported provider: {}. Supported: {:?}",
            config.provider_name, supported_providers
        ));
    }

    if config.model.is_empty() {
        return Err(anyhow::anyhow!("Model is required"));
    }

    let _ = event_tx.send(SseEvent {
        event: "debug_log".to_string(),
        data: serde_json::json!({
            "message": format!("Bootstrap: provider={} model={} tools={} modules={}",
                config.provider_name, config.model,
                config.tools.len(), config.modules.len()),
        }),
        id: None,
    });

    Ok(StageResult {
        stage: HarnessStage::Bootstrap,
        output: serde_json::json!({
            "provider": config.provider_name,
            "model": config.model,
            "tools_count": config.tools.len(),
            "modules": config.modules,
        }),
        score: None,
        error: None,
    })
}
