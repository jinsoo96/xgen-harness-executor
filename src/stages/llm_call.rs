use std::sync::Arc;

use anyhow::Result;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{info, debug, warn};

use crate::events::SseEvent;
use crate::llm::provider::*;
use crate::tools::registry::ToolRegistry;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// LLMCall 단계: 순수 LLM API 호출 (스트리밍)
///
/// ToolExecute 단계와 분리된 순수 LLM 호출.
/// - tool_calls 있으면 → output["has_tool_calls"] = true (상태 머신이 ToolExecute로 점프)
/// - tool_calls 없으면 → 최종 응답 (output["has_tool_calls"] = false, Next 진행)
///
/// ContextCompact가 없는 파이프라인에서는 LLMCall 진입 시 자체 압축 체크 수행.
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    provider: &dyn LlmProvider,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
    cancel_token: &CancellationToken,
    has_context_compact_stage: bool,
) -> Result<StageResult> {
    if cancel_token.is_cancelled() {
        return Err(anyhow::anyhow!("Execution cancelled"));
    }

    // ContextCompact 단계가 파이프라인에 없으면 여기서 자체 압축 체크
    if !has_context_compact_stage {
        let provider_name = &config.provider_name;
        let mut cwm = crate::context::window::ContextWindowManager::new(provider_name);
        let mut rag = context.rag_context.clone();
        if cwm.check_and_compact(&context.system_prompt, &mut context.messages, &mut rag) {
            context.rag_context = rag;
        }
    }

    let tool_registry: Option<Arc<ToolRegistry>> = context.tool_registry.clone();
    let tools: Vec<ToolDefinition> = if let Some(ref registry) = tool_registry {
        registry
            .get_tools_for_role(&crate::tools::registry::AgentRole::Generator)
            .into_iter()
            .cloned()
            .collect()
    } else {
        vec![]
    };

    let call_count = context.llm_call_count + 1;
    context.llm_call_count = call_count;

    debug!(call = call_count, "LLMCall: invoking LLM API");

    let _ = event_tx.send(SseEvent {
        event: "debug_log".to_string(),
        data: serde_json::json!({
            "message": format!("LLMCall #{} | model={} | messages={} | tools={}",
                call_count,
                context.model_override.as_deref().unwrap_or(&config.model),
                context.messages.len(),
                tools.len()),
        }),
        id: None,
    });

    let messages: Vec<ChatMessage> = context
        .messages
        .iter()
        .map(|m| {
            let role = m["role"].as_str().unwrap_or("user").to_string();
            let content_str = m["content"].as_str().unwrap_or("").to_string();

            let tool_calls = if role == "assistant" {
                m["tool_calls"].as_array().map(|arr| {
                    arr.iter().filter_map(|tc| {
                        let id = tc["id"].as_str()?.to_string();
                        let name = tc["function"]["name"].as_str()
                            .or_else(|| tc["name"].as_str())?.to_string();
                        let input: serde_json::Value = tc["function"]["arguments"]
                            .as_str()
                            .and_then(|s| serde_json::from_str(s).ok())
                            .unwrap_or_else(|| tc["input"].clone());
                        Some(ToolCall { id, name, input })
                    }).collect::<Vec<_>>()
                }).filter(|v: &Vec<_>| !v.is_empty())
            } else {
                None
            };

            let tool_call_id = if role == "tool" {
                m["tool_call_id"].as_str().map(|s| s.to_string())
            } else {
                None
            };

            ChatMessage {
                role,
                content: MessageContent::Text(content_str),
                tool_calls,
                tool_call_id,
            }
        })
        .collect();

    let request = ChatRequest {
        model: context.model_override.clone().unwrap_or_else(|| config.model.clone()),
        messages,
        system: Some(context.system_prompt.clone()),
        temperature: config.temperature,
        max_tokens: context.max_tokens_override.unwrap_or(config.max_tokens),
        tools: if tools.is_empty() { None } else { Some(tools.clone()) },
    };

    // --- Exponential backoff retry loop ---
    const MAX_RETRIES: u32 = 3;
    let mut last_error: Option<anyhow::Error> = None;

    let response = 'retry: {
        for attempt in 0..=MAX_RETRIES {
            if cancel_token.is_cancelled() {
                return Err(anyhow::anyhow!("Execution cancelled"));
            }

            // 재시도 시 대기
            if attempt > 0 {
                let err_msg = last_error
                    .as_ref()
                    .map(|e| e.to_string())
                    .unwrap_or_default();
                let err_lower = err_msg.to_lowercase();

                // Context overflow → 재시도 안 함, ContextCompact가 처리하도록 전파
                if err_lower.contains("prompt_too_long")
                    || (err_lower.contains("context") && err_lower.contains("too long"))
                {
                    warn!(attempt, "LLMCall: context overflow detected, not retrying");
                    break;
                }

                // 대기 시간 결정
                let backoff_secs = if err_lower.contains("rate") && err_lower.contains("limit") {
                    // Rate limit → 더 긴 대기 (10s base * 2^(attempt-1))
                    let base = 10u64;
                    base * (1u64 << (attempt - 1).min(3))
                } else if err_lower.contains("timeout") {
                    // Timeout → 즉시 재시도 (0초)
                    0u64
                } else {
                    // 일반 에러 / 529 overload → 지수 백오프 (1s, 2s, 4s)
                    1u64 << (attempt - 1).min(4)
                };

                let _ = event_tx.send(SseEvent {
                    event: "debug_log".to_string(),
                    data: serde_json::json!({
                        "message": format!(
                            "LLMCall: retry {}/{} after {}s | error: {}",
                            attempt, MAX_RETRIES, backoff_secs,
                            err_msg.chars().take(200).collect::<String>()
                        ),
                    }),
                    id: None,
                });

                warn!(
                    attempt,
                    backoff_secs,
                    error = %err_msg.chars().take(200).collect::<String>(),
                    "LLMCall: retrying"
                );

                if backoff_secs > 0 {
                    tokio::time::sleep(std::time::Duration::from_secs(backoff_secs)).await;
                }
            }

            // 매 시도마다 새 채널 생성 (provider가 chunk_tx 소유권을 가져감)
            let (chunk_tx, mut chunk_rx) = mpsc::unbounded_channel::<StreamChunk>();

            let result = if tools.is_empty() {
                provider.chat_stream(request.clone(), chunk_tx).await
            } else {
                provider.chat_stream_with_tools(request.clone(), tools.clone(), chunk_tx).await
            };

            // 스트리밍 청크 → SSE (성공/실패 무관하게 이미 전송된 청크 drain)
            while let Ok(chunk) = chunk_rx.try_recv() {
                match chunk {
                    StreamChunk::Text(text) => {
                        let _ = event_tx.send(SseEvent {
                            event: "message".to_string(),
                            data: serde_json::json!({"type": "text", "text": text}),
                            id: None,
                        });
                    }
                    StreamChunk::ToolCallStart { id, name } => {
                        let _ = event_tx.send(SseEvent {
                            event: "tool_call".to_string(),
                            data: serde_json::json!({"type": "start", "tool_id": id, "tool_name": name}),
                            id: None,
                        });
                    }
                    StreamChunk::ToolCallEnd { id } => {
                        let _ = event_tx.send(SseEvent {
                            event: "tool_call".to_string(),
                            data: serde_json::json!({"type": "end", "tool_id": id}),
                            id: None,
                        });
                    }
                    _ => {}
                }
            }

            match result {
                Ok(resp) => break 'retry resp,
                Err(e) => {
                    let err_lower = e.to_string().to_lowercase();
                    let is_retryable = err_lower.contains("529")
                        || err_lower.contains("overloaded")
                        || (err_lower.contains("rate") && err_lower.contains("limit"))
                        || err_lower.contains("timeout")
                        || err_lower.contains("connection")
                        || err_lower.contains("502")
                        || err_lower.contains("503");

                    if !is_retryable || attempt == MAX_RETRIES {
                        // 재시도 불가능하거나 최대 시도 도달 → 에러 전파
                        return Err(e);
                    }
                    last_error = Some(e);
                }
            }
        }

        // MAX_RETRIES 모두 소진 (break로 탈출 못한 경우 = context overflow 등)
        return Err(last_error.unwrap_or_else(|| anyhow::anyhow!("LLM call failed after retries")));
    };
    // --- End retry loop ---
    // 도구가 0개인데 LLM이 tool_calls를 hallucination으로 생성한 경우 → 무시
    let has_tool_calls = if !response.tool_calls.is_empty() && tools.is_empty() {
        warn!(
            ghost_tools = response.tool_calls.len(),
            "LLMCall: LLM generated tool_calls but no tools available — ignoring"
        );
        let _ = event_tx.send(SseEvent {
            event: "debug_log".to_string(),
            data: serde_json::json!({
                "message": format!(
                    "LLMCall: tool_calls {} 무시 (도구 0개, hallucination)",
                    response.tool_calls.iter().map(|tc| tc.name.as_str()).collect::<Vec<_>>().join(", ")
                ),
            }),
            id: None,
        });
        false
    } else {
        !response.tool_calls.is_empty()
    };

    if has_tool_calls {
        // assistant 메시지 + tool_calls를 컨텍스트에 추가
        // OpenAI 형식: tool_calls[].function.{name, arguments}
        let tool_calls_json: Vec<serde_json::Value> = response.tool_calls.iter().map(|tc| {
            serde_json::json!({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": serde_json::to_string(&tc.input).unwrap_or_default(),
                }
            })
        }).collect();

        context.messages.push(serde_json::json!({
            "role": "assistant",
            "content": response.content,
            "tool_calls": tool_calls_json,
        }));

        for tc in &response.tool_calls {
            let _ = event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!("LLMCall: tool_call queued: {}", tc.name),
                }),
                id: None,
            });
        }

        info!(
            tools = response.tool_calls.len(),
            "LLMCall: tool calls queued → ToolExecute"
        );
    } else {
        // 최종 응답
        context.last_output = serde_json::json!({
            "text": response.content,
            "usage": response.usage,
            "llm_calls": call_count,
        });

        info!("LLMCall: final response received (no tool calls)");
    }

    // 토큰 누적
    context.total_input_tokens += response.usage.input_tokens as u64;
    context.total_output_tokens += response.usage.output_tokens as u64;

    Ok(StageResult {
        stage: HarnessStage::LLMCall,
        output: serde_json::json!({
            "has_tool_calls": has_tool_calls,
            "call_count": call_count,
        }),
        score: None,
        error: None,
    })
}
