use std::sync::Arc;

use anyhow::Result;
use tokio::sync::{mpsc, Mutex};
use tokio_util::sync::CancellationToken;
use tracing::{info, debug, warn};

use crate::events::SseEvent;
use crate::llm::provider::*;
use crate::mcp::client::McpClientManager;
use crate::tools::orchestration::ToolOrchestrator;
use crate::tools::registry::ToolRegistry;
use crate::state_machine::agent_executor::{AgentConfig, ExecutionContext};
use crate::state_machine::stage::{HarnessStage, StageResult};

/// Execute 단계: LLM API 호출 + 도구 루프 (스트리밍)
/// 핵심 실행 단계 — Claude Code의 Step 7~8에 해당
pub async fn execute(
    config: &AgentConfig,
    context: &mut ExecutionContext,
    provider: &dyn LlmProvider,
    event_tx: &mpsc::UnboundedSender<SseEvent>,
    cancel_token: &CancellationToken,
) -> Result<StageResult> {
    info!("Execute stage: running LLM + tool loop");

    let max_iterations = 15; // Claude Code 기본값
    let mut iteration = 0;

    // MCP 도구 매니저 (컨텍스트에 있으면 사용, 없으면 도구 없이 실행)
    let mcp_manager: Option<Arc<Mutex<McpClientManager>>> = context.mcp_manager.clone();
    let tool_registry: Option<Arc<ToolRegistry>> = context.tool_registry.clone();

    // LLM에 전달할 도구 정의
    let tools: Vec<ToolDefinition> = if let Some(ref registry) = tool_registry {
        registry
            .get_tools_for_role(&crate::tools::registry::AgentRole::Generator)
            .into_iter()
            .cloned()
            .collect()
    } else {
        vec![]
    };

    loop {
        if cancel_token.is_cancelled() {
            return Err(anyhow::anyhow!("Execution cancelled"));
        }

        if iteration >= max_iterations {
            info!("Max iterations reached ({})", max_iterations);
            break;
        }

        iteration += 1;
        debug!(iteration = iteration, "LLM call iteration");

        // 프론트엔드 실행 로그에 상세 정보 전송
        let _ = event_tx.send(SseEvent {
            event: "debug_log".to_string(),
            data: serde_json::json!({
                "message": format!("LLM 호출 #{} | model={} | messages={} | tools={}",
                    iteration,
                    context.model_override.as_deref().unwrap_or(&config.model),
                    context.messages.len(),
                    if tools.is_empty() { 0 } else { tools.len() }),
            }),
            id: None,
        });

        // ContextWindowManager: 컨텍스트 예산 체크 + 필요 시 자동 압축
        {
            let provider_name = &config.provider_name;
            let mut cwm = crate::context::window::ContextWindowManager::new(provider_name);
            let mut rag = context.rag_context.clone();
            if cwm.check_and_compact(
                &context.system_prompt,
                &mut context.messages,
                &mut rag,
            ) {
                context.rag_context = rag;
                info!("Context compacted (stage 1-2) before LLM call");
            }

            // 3단계: 1-2단계 압축 후에도 예산 초과 → LLM 요약
            if cwm.needs_llm_summary() {
                info!("Context still over budget, attempting LLM summary");
                let summary_result = summarize_conversation(
                    provider,
                    &context.system_prompt,
                    &context.messages,
                    config,
                ).await;

                match summary_result {
                    Ok(summary) => {
                        // 기존 메시지를 요약 + 최근 2개로 교체
                        let recent: Vec<serde_json::Value> = if context.messages.len() > 2 {
                            context.messages[context.messages.len() - 2..].to_vec()
                        } else {
                            context.messages.clone()
                        };

                        context.messages = vec![serde_json::json!({
                            "role": "user",
                            "content": format!("[이전 대화 요약]\n{}", summary)
                        })];
                        context.messages.extend(recent);

                        cwm.on_summary_success();
                        info!("LLM summary applied, messages reduced");
                    }
                    Err(e) => {
                        cwm.on_summary_failure();
                        warn!("LLM summary failed: {}", e);
                    }
                }
            }
        }

        // 메시지 구성 (tool_calls / tool_call_id 보존)
        let messages: Vec<ChatMessage> = context
            .messages
            .iter()
            .map(|m| {
                let role = m["role"].as_str().unwrap_or("user").to_string();
                let content_str = m["content"].as_str().unwrap_or("").to_string();

                // assistant 메시지 + tool_calls
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
                            Some(crate::llm::provider::ToolCall { id, name, input })
                        }).collect::<Vec<_>>()
                    }).filter(|v: &Vec<_>| !v.is_empty())
                } else {
                    None
                };

                // tool 메시지 → tool_call_id
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

        // 스트리밍 채널
        let (chunk_tx, mut chunk_rx) = mpsc::unbounded_channel::<StreamChunk>();

        let request = ChatRequest {
            model: context.model_override.clone().unwrap_or_else(|| config.model.clone()),
            messages,
            system: Some(context.system_prompt.clone()),
            temperature: config.temperature,
            max_tokens: context.max_tokens_override.unwrap_or(config.max_tokens),
            tools: if tools.is_empty() { None } else { Some(tools.clone()) },
        };

        // 직접 스트리밍 호출
        let response = if tools.is_empty() {
            provider.chat_stream(request, chunk_tx).await
        } else {
            provider
                .chat_stream_with_tools(request, tools.clone(), chunk_tx)
                .await
        };

        // 스트리밍 청크를 SSE 이벤트로 전달 (drain)
        while let Ok(chunk) = chunk_rx.try_recv() {
            match chunk {
                StreamChunk::Text(text) => {
                    let _ = event_tx.send(SseEvent {
                        event: "message".to_string(),
                        data: serde_json::json!({
                            "type": "text",
                            "text": text,
                        }),
                        id: None,
                    });
                }
                StreamChunk::ToolCallStart { id, name } => {
                    let _ = event_tx.send(SseEvent {
                        event: "tool_call".to_string(),
                        data: serde_json::json!({
                            "type": "start",
                            "tool_id": id,
                            "tool_name": name,
                        }),
                        id: None,
                    });
                }
                StreamChunk::ToolCallEnd { id } => {
                    let _ = event_tx.send(SseEvent {
                        event: "tool_call".to_string(),
                        data: serde_json::json!({
                            "type": "end",
                            "tool_id": id,
                        }),
                        id: None,
                    });
                }
                _ => {}
            }
        }

        let response = response?;

        // 도구 호출이 있으면 MCP로 실행 후 다음 이터레이션
        if !response.tool_calls.is_empty() {
            // assistant 메시지 추가
            context.messages.push(serde_json::json!({
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }));

            // 도구 호출 상세 로그
            for tc in &response.tool_calls {
                let input_str = serde_json::to_string(&tc.input).unwrap_or_default();
                let _ = event_tx.send(SseEvent {
                    event: "debug_log".to_string(),
                    data: serde_json::json!({
                        "message": format!("MCP tool_call: {} | args: {}", tc.name, input_str),
                    }),
                    id: None,
                });
            }

            // MCP 도구 호출 실행
            if let Some(ref mcp_mgr) = mcp_manager {
                let results = ToolOrchestrator::execute_tool_calls(
                    mcp_mgr,
                    &response.tool_calls,
                )
                .await;

                for result in results {
                    let content_preview = result.content.clone();
                    let _ = event_tx.send(SseEvent {
                        event: "debug_log".to_string(),
                        data: serde_json::json!({
                            "message": format!("MCP result: {} | error={} | preview: {}",
                                result.tool_name, result.is_error, content_preview),
                        }),
                        id: None,
                    });

                    // 도구 결과 SSE 이벤트
                    let _ = event_tx.send(SseEvent {
                        event: "tool_result".to_string(),
                        data: serde_json::json!({
                            "tool_id": result.tool_call_id,
                            "tool_name": result.tool_name,
                            "is_error": result.is_error,
                            "content_length": result.content.len(),
                        }),
                        id: None,
                    });

                    // 메시지에 도구 결과 추가
                    context.messages.push(serde_json::json!({
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "content": result.content,
                    }));
                }
            } else {
                // MCP 매니저 없음 — stub 결과
                for tool_call in &response.tool_calls {
                    warn!(
                        tool = %tool_call.name,
                        "No MCP manager available, returning stub result"
                    );
                    context.messages.push(serde_json::json!({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": format!(
                            "Tool '{}' called but no MCP server connected. \
                             Configure tools in agent config to enable MCP tool execution.",
                            tool_call.name
                        ),
                    }));
                }
            }

            continue; // 다음 이터레이션 (도구 결과로 다시 LLM 호출)
        }

        // 도구 호출 없음 = 최종 응답
        context.last_output = serde_json::json!({
            "text": response.content,
            "usage": response.usage,
            "iterations": iteration,
        });

        break;
    }

    Ok(StageResult {
        stage: HarnessStage::Execute,
        output: context.last_output.clone(),
        score: None,
        error: None,
    })
}

/// LLM 기반 대화 요약 (ContextWindow 3단계 압축)
async fn summarize_conversation(
    provider: &dyn LlmProvider,
    _system_prompt: &str,
    messages: &[serde_json::Value],
    config: &AgentConfig,
) -> Result<String> {
    let conversation_text: String = messages
        .iter()
        .filter_map(|m| {
            let role = m["role"].as_str().unwrap_or("unknown");
            let content = m["content"].as_str().unwrap_or("");
            if content.is_empty() {
                None
            } else {
                Some(format!("[{}]: {}", role, content))
            }
        })
        .collect::<Vec<_>>()
        .join("\n\n");

    // 대화가 너무 짧으면 요약 불필요
    if conversation_text.len() < 2000 {
        return Err(anyhow::anyhow!("Conversation too short to summarize"));
    }

    let summary_prompt = format!(
        "아래 대화를 핵심 정보만 보존하여 3~5문장으로 요약하세요.\n\
         - 사용자의 원래 요청\n\
         - 수행된 도구 호출과 결과 요약\n\
         - 현재까지의 진행 상황\n\
         - 아직 미완료된 사항\n\n\
         대화:\n{}",
        // 대화가 너무 길면 앞부분 잘라냄
        if conversation_text.len() > 20_000 {
            &conversation_text[conversation_text.len() - 20_000..]
        } else {
            &conversation_text
        }
    );

    let request = ChatRequest {
        model: config.model.clone(),
        messages: vec![ChatMessage {
            role: "user".to_string(),
            content: MessageContent::Text(summary_prompt),
            tool_calls: None,
            tool_call_id: None,
        }],
        system: Some("You are a concise summarizer. Output only the summary, nothing else.".to_string()),
        temperature: 0.1,
        max_tokens: 1024,
        tools: None,
    };

    let response = provider.chat(request).await?;
    Ok(response.content)
}
