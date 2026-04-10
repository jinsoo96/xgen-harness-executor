use anyhow::Result;
use chrono::Utc;
use tokio::sync::mpsc;
use tracing::{info, warn};

use crate::events::SseEvent;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};
use crate::workflow::db::{DbManager, ExecutionLog};

/// 출력 텍스트 최대 길이 (DB 저장 시 잘라냄)
const MAX_OUTPUT_CHARS: usize = 10_000;

/// MemoryWrite 단계: 실행 결과를 DB(harness_execution_log)에 저장
/// MemoryRead에서 읽은 데이터를 완료 후 업데이트
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!("MemoryWrite: persisting execution result");

    let output_text = context.last_output
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let model = context.model_override.as_deref().unwrap_or(&config.model);
    let llm_calls = context.llm_call_count;
    let eval_score = context.eval_score;
    let duration_ms = context.start_time.elapsed().as_millis() as i64;
    let total_input_tokens = context.total_input_tokens;
    let total_output_tokens = context.total_output_tokens;
    let total_tokens = total_input_tokens + total_output_tokens;

    // 비용 추정 (모델별 공개 가격 기준, USD)
    let cost_usd = estimate_cost(model, total_input_tokens, total_output_tokens);

    // ── 메트릭 SSE 이벤트 ─────────────────────────────────────────
    let _ = event_tx.send(SseEvent {
        event: "metrics".to_string(),
        data: serde_json::json!({
            "duration_ms": duration_ms,
            "llm_calls": llm_calls,
            "total_tokens": total_tokens,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cost_usd": cost_usd,
            "eval_score": eval_score,
            "model": model,
        }),
        id: None,
    });

    // ── SSE 이벤트 전송 (기존 동작 유지) ──────────────────────────
    let _ = event_tx.send(SseEvent {
        event: "memory_write".to_string(),
        data: serde_json::json!({
            "provider": config.provider_name,
            "model": model,
            "llm_calls": llm_calls,
            "output_chars": output_text.len(),
            "eval_score": eval_score,
        }),
        id: None,
    });

    // ── DB 저장 ───────────────────────────────────────────────────
    let db_saved = match persist_to_db(config, context, &output_text, model, llm_calls, eval_score, duration_ms, total_input_tokens, total_output_tokens).await {
        Ok(log_id) => {
            info!(log_id, "MemoryWrite: execution log saved to DB");
            context.execution_log_id = Some(log_id);
            let _ = event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!(
                        "MemoryWrite: DB saved (id={}) llm_calls={} output={}chars score={:?}",
                        log_id, llm_calls, output_text.len(), eval_score
                    ),
                }),
                id: None,
            });
            true
        }
        Err(reason) => {
            warn!(reason = %reason, "MemoryWrite: DB write skipped");
            let _ = event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!(
                        "MemoryWrite: DB skipped ({}) llm_calls={} output={}chars score={:?}",
                        reason, llm_calls, output_text.len(), eval_score
                    ),
                }),
                id: None,
            });
            false
        }
    };

    Ok(StageResult {
        stage: HarnessStage::MemoryWrite,
        output: serde_json::json!({
            "output_chars": output_text.len(),
            "llm_calls": llm_calls,
            "db_saved": db_saved,
            "duration_ms": duration_ms,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
        }),
        score: None,
        error: None,
    })
}

/// 모델별 비용 추정 (USD, 공개 API 가격 기준)
fn estimate_cost(model: &str, input_tokens: u64, output_tokens: u64) -> f64 {
    // 가격: USD per 1M tokens
    let (input_price, output_price) = if model.contains("opus") {
        (15.0_f64, 75.0_f64)
    } else if model.contains("sonnet") {
        (3.0_f64, 15.0_f64)
    } else if model.contains("haiku") {
        (0.25_f64, 1.25_f64)
    } else {
        (3.0_f64, 15.0_f64) // 기본값: sonnet 급
    };
    let input_cost = (input_tokens as f64 / 1_000_000.0) * input_price;
    let output_cost = (output_tokens as f64 / 1_000_000.0) * output_price;
    ((input_cost + output_cost) * 100.0).round() / 100.0 // 소수점 2자리
}

/// DB에 실행 로그를 저장한다. DATABASE_URL이 없거나 연결 실패 시 에러 반환 (크래시 아님).
async fn persist_to_db(
    config: &AgentConfig,
    context: &ExecutionContext,
    output_text: &str,
    model: &str,
    llm_calls: u32,
    eval_score: Option<f64>,
    duration_ms: i64,
    total_input_tokens: u64,
    total_output_tokens: u64,
) -> std::result::Result<i64, String> {
    // DATABASE_URL 확인
    let database_url = std::env::var("DATABASE_URL")
        .map_err(|_| "DATABASE_URL not set".to_string())?;

    // DB 연결
    let db = DbManager::new(&database_url).await
        .map_err(|e| format!("DB connect failed: {}", e))?;

    // input에서 workflow_id, execution_id 추출
    let workflow_id = context.input
        .get("workflow_id")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    let interaction_id = context.input
        .get("execution_id")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    // agent_id: input에 있으면 사용, 없으면 "default"
    let agent_id = context.input
        .get("agent_id")
        .and_then(|v| v.as_str())
        .unwrap_or("default")
        .to_string();

    // 출력 텍스트 truncate (10K chars)
    let truncated_output = if output_text.len() > MAX_OUTPUT_CHARS {
        format!("{}... [truncated]", &output_text[..MAX_OUTPUT_CHARS])
    } else {
        output_text.to_string()
    };

    // token_usage JSON (누적 토큰 + 평가 점수)
    let token_usage = Some(serde_json::json!({
        "llm_call_count": llm_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "eval_score": eval_score,
    }));

    // user_id: Python이 payload에 넣어준 값 사용
    let user_id = context.input
        .get("user_id")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);

    let log = ExecutionLog {
        workflow_id,
        interaction_id,
        user_id,
        agent_id,
        agent_name: config.provider_name.clone(),
        stage: "memory_write".to_string(),
        input_data: serde_json::json!({
            "provider": config.provider_name,
            "model": model,
        }),
        output_data: serde_json::json!({
            "content": truncated_output,
            "llm_call_count": llm_calls,
            "eval_score": eval_score,
            "total_tokens": total_input_tokens + total_output_tokens,
        }),
        status: "completed".to_string(),
        duration_ms: Some(duration_ms),
        token_usage,
        created_at: Utc::now(),
    };

    db.save_execution_log(&log).await
}
