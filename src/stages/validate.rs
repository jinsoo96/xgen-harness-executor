use anyhow::Result;
use chrono::Utc;
use tokio::sync::mpsc;
use tracing::{info, warn};

use crate::events::SseEvent;
use crate::llm::provider::*;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};
use crate::workflow::db::{DbManager, ExecutionLog};

/// Evaluator 시스템 프롬프트
const EVALUATOR_SYSTEM_PROMPT: &str = r#"You are an independent evaluator. Your job is to assess the quality of an AI assistant's response.

## Evaluation Criteria

Score each criterion from 0.0 to 1.0:

1. **Relevance** (weight: 0.3)
   - Does the response directly address the user's question?
   - Is all information in the response pertinent?

2. **Completeness** (weight: 0.3)
   - Are all parts of the question answered?
   - Is sufficient detail provided?

3. **Accuracy** (weight: 0.2)
   - Is the information factually correct?
   - Are claims properly grounded in the provided context?

4. **Clarity** (weight: 0.2)
   - Is the response clear and well-structured?
   - Is the language appropriate for the user?

## Output Format

Respond with ONLY a JSON object:
```json
{
  "relevance": 0.0-1.0,
  "completeness": 0.0-1.0,
  "accuracy": 0.0-1.0,
  "clarity": 0.0-1.0,
  "overall": 0.0-1.0,
  "verdict": "pass" | "retry",
  "feedback": "Brief explanation for the score"
}
```

Be skeptical. A score of 0.7+ means genuinely good quality. Do not inflate scores."#;

/// Validate 단계: 독립 평가 게이트
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    provider: &dyn LlmProvider,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
) -> Result<StageResult> {
    info!("Validate stage: running independent evaluation");

    let last_output_text = context
        .last_output
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    if last_output_text.is_empty() {
        return Ok(StageResult {
            stage: HarnessStage::Validate,
            output: serde_json::json!({"verdict": "pass", "reason": "no output to evaluate"}),
            score: Some(1.0),
            error: None,
        });
    }

    // 원래 질문 추출
    let user_question = context
        .messages
        .first()
        .and_then(|m| m["content"].as_str())
        .unwrap_or("");

    let eval_message = format!(
        "## User Question\n{}\n\n## Assistant Response\n{}\n\nEvaluate the response quality against ALL criteria.",
        user_question, last_output_text
    );

    let request = ChatRequest {
        model: config.model.clone(),
        messages: vec![ChatMessage {
            role: "user".to_string(),
            content: MessageContent::Text(eval_message),
            tool_calls: None,
            tool_call_id: None,
        }],
        system: Some(EVALUATOR_SYSTEM_PROMPT.to_string()),
        temperature: 0.1, // 평가는 결정적으로
        max_tokens: 512,
        tools: None,
    };

    let response = provider.chat(request).await?;

    // JSON 파싱 시도
    let eval_result: serde_json::Value = serde_json::from_str(&response.content)
        .unwrap_or_else(|_| {
            // JSON 추출 시도 (마크다운 코드블록 안에 있을 수 있음)
            extract_json_from_text(&response.content)
                .unwrap_or(serde_json::json!({
                    "overall": 0.5,
                    "verdict": "retry",
                    "feedback": "Failed to parse evaluation"
                }))
        });

    let overall_score = eval_result["overall"].as_f64().unwrap_or(0.5);
    let verdict = eval_result["verdict"]
        .as_str()
        .unwrap_or(if overall_score >= config.eval_threshold { "pass" } else { "retry" });

    // 점수를 컨텍스트에 저장
    context.eval_score = Some(overall_score);

    // 메모리 자동 추출: 평가 통과 시 중요 사실을 DB에 기록
    if overall_score >= config.eval_threshold {
        extract_and_persist_memory(config, context, last_output_text, user_question).await;
    }

    // 평가 이벤트 전송
    let _ = event_tx.send(SseEvent {
        event: "evaluation".to_string(),
        data: serde_json::json!({
            "score": overall_score,
            "verdict": verdict,
            "feedback": eval_result["feedback"],
        }),
        id: None,
    });

    info!(
        score = overall_score,
        verdict = verdict,
        "Evaluation complete"
    );

    Ok(StageResult {
        stage: HarnessStage::Validate,
        output: eval_result,
        score: Some(overall_score),
        error: None,
    })
}

/// 응답에서 중요 사실을 추출하여 DB에 저장 (메모리 자동 추출)
///
/// 규칙 기반 추출:
/// - 숫자/버전이 포함된 문장 (설정값, 결과 수치)
/// - URL/경로가 포함된 문장 (리소스 위치)
/// - "중요", "주의", "필수" 등 강조 키워드 포함 문장
async fn extract_and_persist_memory(
    config: &AgentConfig,
    context: &ExecutionContext,
    output_text: &str,
    user_question: &str,
) {
    // 중요 사실 추출 (간단한 규칙 기반)
    let facts: Vec<&str> = output_text
        .lines()
        .filter(|line| {
            let l = line.trim();
            if l.len() < 10 || l.len() > 500 {
                return false;
            }
            // 숫자/버전 패턴, URL, 경로, 강조 키워드
            let has_number = l.chars().any(|c| c.is_ascii_digit());
            let has_url = l.contains("http") || l.contains("://") || l.starts_with('/');
            let has_emphasis = l.contains("중요") || l.contains("필수") || l.contains("주의")
                || l.contains("WARNING") || l.contains("NOTE") || l.contains("IMPORTANT");
            let is_code_like = l.starts_with('-') || l.starts_with('*') || l.starts_with("  ");
            has_number || has_url || has_emphasis || is_code_like
        })
        .take(20)
        .collect();

    if facts.is_empty() {
        return;
    }

    let facts_text = facts.join("\n");

    // DATABASE_URL이 없으면 조용히 스킵
    let database_url = match std::env::var("DATABASE_URL") {
        Ok(u) => u,
        Err(_) => return,
    };

    let db = match DbManager::new(&database_url).await {
        Ok(d) => d,
        Err(e) => {
            warn!(error = %e, "Memory extract: DB connect failed");
            return;
        }
    };

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

    let log = ExecutionLog {
        workflow_id,
        interaction_id,
        user_id: 0,
        agent_id: context.input
            .get("agent_id")
            .and_then(|v| v.as_str())
            .unwrap_or("default")
            .to_string(),
        agent_name: config.provider_name.clone(),
        stage: "memory_extract".to_string(),
        input_data: serde_json::json!({ "question": user_question }),
        output_data: serde_json::json!({
            "extracted_facts": facts_text,
            "fact_count": facts.len(),
        }),
        status: "completed".to_string(),
        duration_ms: None,
        token_usage: None,
        created_at: Utc::now(),
    };

    match db.save_execution_log(&log).await {
        Ok(id) => info!(id, facts = facts.len(), "Memory extract: facts saved to DB"),
        Err(e) => warn!(error = %e, "Memory extract: DB save failed"),
    }
}

fn extract_json_from_text(text: &str) -> Option<serde_json::Value> {
    // ```json ... ``` 블록 추출
    if let Some(start) = text.find("```json") {
        let content_start = start + 7;
        if let Some(end) = text[content_start..].find("```") {
            let json_str = text[content_start..content_start + end].trim();
            return serde_json::from_str(json_str).ok();
        }
    }
    // { ... } 직접 추출
    if let Some(start) = text.find('{') {
        if let Some(end) = text.rfind('}') {
            let json_str = &text[start..=end];
            return serde_json::from_str(json_str).ok();
        }
    }
    None
}
