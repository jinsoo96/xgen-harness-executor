use anyhow::Result;
use tokio::sync::mpsc;
use tracing::info;

use crate::events::SseEvent;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// Decide 단계: 재시도/통과/에러 결정
/// 상태 머신의 전이 결정은 AgentStateMachine.decide_transition()에서 수행.
/// 여기서는 점수와 컨텍스트를 기반으로 StageResult를 생성만 함.
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    let score = context.eval_score.unwrap_or(1.0);
    let should_retry = score < config.eval_threshold;

    info!(
        score = score,
        threshold = config.eval_threshold,
        decision = if should_retry { "retry" } else { "pass" },
        "Decide stage"
    );

    // 결정 이벤트 전송
    let _ = event_tx.send(SseEvent {
        event: "decision".to_string(),
        data: serde_json::json!({
            "score": score,
            "threshold": config.eval_threshold,
            "decision": if should_retry { "retry" } else { "pass" },
        }),
        id: None,
    });

    // retry인 경우 feedback을 컨텍스트에 추가 (다음 Plan/Execute에서 참조)
    if should_retry {
        if let Some(last_result) = context.tool_results.last() {
            if let Some(feedback) = last_result.get("feedback").and_then(|v| v.as_str()) {
                context.messages.push(serde_json::json!({
                    "role": "user",
                    "content": format!(
                        "Your previous response scored {:.2}/{:.2}. Feedback: {}. Please improve.",
                        score, config.eval_threshold, feedback
                    ),
                }));
            }
        }
    }

    Ok(StageResult {
        stage: HarnessStage::Decide,
        output: serde_json::json!({
            "decision": if should_retry { "retry" } else { "pass" },
            "score": score,
        }),
        score: Some(score), // AgentStateMachine이 이 score로 JumpTo/Next 결정
        error: None,
    })
}
