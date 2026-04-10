use std::collections::HashMap;
use std::sync::Arc;

use anyhow::Result;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{info, warn, error};

use crate::events::SseEvent;
use crate::llm::provider::LlmProvider;
use crate::mcp::client::{McpClientManager, McpTransport};
use crate::tools::registry::ToolRegistry;
use crate::state_machine::stage::{HarnessStage, StageResult, StageTransition};
use crate::stages;

/// 에이전트별 독립 하네스 실행기.
/// 각 에이전트가 자신만의 상태 머신 인스턴스를 가진다.
pub struct AgentStateMachine {
    pub agent_id: String,
    pub agent_name: String,
    /// 사용자가 선택한 단계들 (체크리스트)
    stages: Vec<HarnessStage>,
    /// 현재 실행 포인터
    pointer: usize,
    /// Decide→Plan 재시도 횟수 (max_retries 적용)
    retry_counts: HashMap<HarnessStage, u32>,
    /// LLMCall↔ToolExecute 루프 횟수 (도구 루프는 별도 제한: 최대 20회)
    tool_loop_count: u32,
    /// 설정
    config: AgentConfig,
    /// LLM 프로바이더
    provider: Arc<dyn LlmProvider>,
    /// SSE 이벤트 송신 채널
    event_tx: mpsc::UnboundedSender<SseEvent>,
    /// 취소 토큰
    cancel_token: CancellationToken,
    /// 단계별 결과 누적
    stage_results: Vec<StageResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentConfig {
    pub provider_name: String,
    pub model: String,
    pub system_prompt: String,
    pub temperature: f64,
    pub max_tokens: u32,
    pub max_retries: u32,
    pub eval_threshold: f64,
    pub context_budget: u64,
    /// MCP 도구 URI 목록
    pub tools: Vec<String>,
    /// 활성 모듈 목록
    pub modules: Vec<String>,
}

use serde::{Deserialize, Serialize};

/// 복구 후 동작 결정
enum RecoveryOutcome {
    /// 같은 단계 재시도 (pointer 유지)
    Retry,
    /// 포기 (에러 전파)
    GiveUp,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            provider_name: "anthropic".to_string(),
            model: "claude-sonnet-4-6".to_string(),
            system_prompt: String::new(),
            temperature: 0.7,
            max_tokens: 8192,
            max_retries: 3,
            eval_threshold: 0.7,
            context_budget: 200_000,
            tools: vec![],
            modules: vec![],
        }
    }
}

impl AgentStateMachine {
    pub fn new(
        agent_id: String,
        agent_name: String,
        stages: Vec<HarnessStage>,
        config: AgentConfig,
        provider: Arc<dyn LlmProvider>,
        event_tx: mpsc::UnboundedSender<SseEvent>,
        cancel_token: CancellationToken,
    ) -> Self {
        Self {
            agent_id,
            agent_name,
            stages,
            pointer: 0,
            retry_counts: HashMap::new(),
            tool_loop_count: 0,
            config,
            provider,
            event_tx,
            cancel_token,
            stage_results: Vec::new(),
        }
    }

    /// 상태 머신 실행 — while 루프 (for 루프 아님!)
    pub async fn run(&mut self, input: serde_json::Value) -> Result<serde_json::Value> {
        // ── 입력 복잡도 기반 자동 바이패스 ────────────────────────
        // "hi" 같은 단순 입력에 12단계 전부 돌리는 건 낭비.
        // classify → 현재 파이프라인이 과도하면 다운그레이드.
        // 단, 사용자가 stages를 명시적으로 지정했으면 바이패스 비활성화.
        let stages_explicit = input.get("stages_explicit").and_then(|v| v.as_bool()).unwrap_or(false);
        let input_text = input.get("text").and_then(|v| v.as_str()).unwrap_or("");
        if !stages_explicit {
        if let Some(downgraded_preset) = stages::classify::should_downgrade(
            &self.current_preset_name(),
            input_text,
        ) {
            let old_count = self.stages.len();
            self.stages = HarnessStage::preset(downgraded_preset);
            info!(
                agent_id = %self.agent_id,
                input_len = input_text.len(),
                from = old_count,
                to = self.stages.len(),
                preset = downgraded_preset,
                "Auto-bypass: 입력 복잡도 낮음 → 파이프라인 다운그레이드"
            );

            let _ = self.event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!(
                        "Auto-bypass: {}단계 → {}단계 ({})",
                        old_count, self.stages.len(), downgraded_preset
                    ),
                }),
                id: None,
            });
        }
        } // if !stages_explicit

        info!(
            agent_id = %self.agent_id,
            stages = ?self.stages.iter().map(|s| s.display_name()).collect::<Vec<_>>(),
            tools = ?self.config.tools,
            "Starting agent state machine"
        );

        // MCP 도구 초기화 (tools 배열이 있으면)
        let (mcp_manager_opt, tool_registry_opt) = if self.config.tools.is_empty() {
            (None, None)
        } else {
            match self.init_mcp_clients().await {
                Ok((mgr, registry)) => (Some(mgr), Some(registry)),
                Err(e) => {
                    warn!(agent_id = %self.agent_id, error = %e, "MCP 초기화 실패 — 도구 없이 실행");
                    (None, None)
                }
            }
        };

        let has_context_compact_stage = self.stages.contains(&HarnessStage::ContextCompact);

        let mut context = ExecutionContext {
            input,
            messages: vec![],
            system_prompt: self.config.system_prompt.clone(),
            tool_results: vec![],
            last_output: serde_json::Value::Null,
            eval_score: None,
            mcp_manager: mcp_manager_opt,
            tool_registry: tool_registry_opt,
            recovery_manager: crate::stages::recover::ErrorRecoveryManager::new(),
            max_tokens_override: None,
            model_override: None,
            rag_context: String::new(),
            llm_call_count: 0,
            has_context_compact_stage,
            start_time: std::time::Instant::now(),
            total_input_tokens: 0,
            total_output_tokens: 0,
            execution_log_id: None,
        };

        self.pointer = 0;

        // 핵심: while 루프 — 포인터 기반 상태 머신
        while self.pointer < self.stages.len() {
            // 취소 체크
            if self.cancel_token.is_cancelled() {
                warn!(agent_id = %self.agent_id, "Agent cancelled");
                return Err(anyhow::anyhow!("Agent execution cancelled"));
            }

            let current_stage = self.stages[self.pointer];

            // stage_enter 이벤트 전송
            self.emit_stage_event("stage_enter", current_stage, None).await;

            // 단계 실행
            let result = self.execute_stage(current_stage, &mut context).await;

            match result {
                Ok(stage_result) => {
                    // stage_exit 이벤트 전송
                    self.emit_stage_event("stage_exit", current_stage, Some(&stage_result)).await;
                    self.stage_results.push(stage_result.clone());

                    // TraceSpan 비동기 저장 (execution_log_id가 있으면)
                    if let Some(log_id) = context.execution_log_id {
                        self.save_trace_span_async(
                            log_id,
                            "stage",
                            current_stage.display_name(),
                            None,
                            Some(stage_result.output.clone()),
                            context.start_time.elapsed().as_millis() as i64,
                        );
                    }

                    // 전이 결정
                    let transition = self.decide_transition(current_stage, &stage_result);

                    match transition {
                        StageTransition::Next => {
                            self.pointer += 1;
                        }
                        StageTransition::JumpTo(target) => {
                            // LLMCall↔ToolExecute 도구 루프: 별도 카운터, 최대 20회
                            let is_tool_loop = current_stage == HarnessStage::ToolExecute
                                && target == HarnessStage::LLMCall;

                            if is_tool_loop {
                                self.tool_loop_count += 1;
                                const MAX_TOOL_LOOPS: u32 = 20;
                                if self.tool_loop_count > MAX_TOOL_LOOPS {
                                    warn!(
                                        agent_id = %self.agent_id,
                                        loops = self.tool_loop_count,
                                        "Tool loop limit reached, exiting loop"
                                    );
                                    self.pointer += 1;
                                } else {
                                    info!(
                                        agent_id = %self.agent_id,
                                        loop_count = self.tool_loop_count,
                                        "Tool loop: ToolExecute → LLMCall"
                                    );
                                    if let Some(idx) = self.stages.iter().position(|s| *s == target) {
                                        self.pointer = idx;
                                    } else {
                                        self.pointer += 1;
                                    }
                                }
                            } else {
                                // Decide→Plan 재시도: max_retries 적용
                                let count = self.retry_counts.entry(target).or_insert(0);
                                *count += 1;

                                if *count > self.config.max_retries {
                                    warn!(
                                        agent_id = %self.agent_id,
                                        stage = ?target,
                                        retries = *count,
                                        "Max retries exceeded, proceeding with current result"
                                    );
                                    self.pointer += 1;
                                } else {
                                    info!(
                                        agent_id = %self.agent_id,
                                        target = ?target,
                                        retry = *count,
                                        "Jumping back for retry"
                                    );
                                    if let Some(idx) = self.stages.iter().position(|s| *s == target) {
                                        self.pointer = idx;
                                    } else {
                                        self.pointer += 1;
                                    }
                                }
                            }
                        }
                        StageTransition::Complete(output) => {
                            context.last_output = output;
                            break;
                        }
                        StageTransition::Error(msg) => {
                            error!(agent_id = %self.agent_id, error = %msg, "Stage error");
                            self.emit_stage_event("stage_error", current_stage, None).await;
                            return Err(anyhow::anyhow!(msg));
                        }
                    }
                }
                Err(e) => {
                    error!(
                        agent_id = %self.agent_id,
                        stage = ?current_stage,
                        error = %e,
                        "Stage execution failed"
                    );
                    self.emit_stage_event("stage_error", current_stage, None).await;

                    // 에러 리커버리 모듈이 활성화되어 있으면 복구 시도
                    if self.config.modules.contains(&"error_recovery".to_string()) {
                        match self.apply_recovery(current_stage, &e, &mut context).await {
                            RecoveryOutcome::Retry => continue, // pointer 유지, 재시도
                            RecoveryOutcome::GiveUp => {}
                        }
                    }

                    return Err(e);
                }
            }
        }

        info!(agent_id = %self.agent_id, "Agent state machine completed");
        Ok(context.last_output)
    }

    /// 개별 단계 실행 — 각 단계는 독립 모듈
    async fn execute_stage(
        &self,
        stage: HarnessStage,
        context: &mut ExecutionContext,
    ) -> Result<StageResult> {
        match stage {
            // ── Phase 1: 초기화 ──────────────────────────────
            HarnessStage::Bootstrap => {
                stages::bootstrap::execute(&self.config, context, &self.event_tx).await
            }
            HarnessStage::MemoryRead => {
                stages::memory_read::execute(&self.config, context, &self.event_tx).await
            }
            HarnessStage::ContextBuild => {
                stages::context_build::execute(&self.config, context, &self.event_tx).await
            }

            // ── Phase 2: 계획 ──────────────────────────────
            HarnessStage::Plan => {
                stages::plan::execute(&self.config, context, self.provider.as_ref(), &self.event_tx).await
            }
            HarnessStage::ToolDiscovery => {
                stages::tool_discovery::execute(&self.config, context, &self.event_tx).await
            }

            // ── Phase 3: 실행 ──────────────────────────────
            HarnessStage::ContextCompact => {
                stages::context_compact::execute(&self.config, context, self.provider.as_ref(), &self.event_tx).await
            }
            HarnessStage::LLMCall => {
                stages::llm_call::execute(
                    &self.config,
                    context,
                    self.provider.as_ref(),
                    &self.event_tx,
                    &self.cancel_token,
                    context.has_context_compact_stage,
                ).await
            }
            HarnessStage::ToolExecute => {
                stages::tool_execute::execute(&self.config, context, &self.event_tx).await
            }

            // ── Phase 4: 검증 ──────────────────────────────
            HarnessStage::Validate => {
                stages::validate::execute(&self.config, context, self.provider.as_ref(), &self.event_tx).await
            }
            HarnessStage::Decide => {
                stages::decide::execute(&self.config, context, &self.event_tx).await
            }

            // ── Phase 5: 마무리 ────────────────────────────
            HarnessStage::MemoryWrite => {
                stages::memory_write::execute(&self.config, context, &self.event_tx).await
            }
            HarnessStage::Complete => {
                let duration_ms = context.start_time.elapsed().as_millis() as u64;
                let total_tokens = context.total_input_tokens + context.total_output_tokens;

                // metrics 이벤트 발행
                let _ = self.event_tx.send(SseEvent {
                    event: "metrics".to_string(),
                    data: serde_json::json!({
                        "duration_ms": duration_ms,
                        "input_tokens": context.total_input_tokens,
                        "output_tokens": context.total_output_tokens,
                        "total_tokens": total_tokens,
                        "llm_calls": context.llm_call_count,
                        "model": context.model_override.as_deref().unwrap_or(&self.config.model),
                        "eval_score": context.eval_score,
                    }),
                    id: None,
                });

                // last_output에 메트릭 병합
                let mut output = context.last_output.clone();
                if let Some(obj) = output.as_object_mut() {
                    obj.insert("duration_ms".to_string(), serde_json::json!(duration_ms));
                    obj.insert("total_tokens".to_string(), serde_json::json!(total_tokens));
                    obj.insert("llm_calls".to_string(), serde_json::json!(context.llm_call_count));
                }

                Ok(StageResult {
                    stage: HarnessStage::Complete,
                    output,
                    score: context.eval_score,
                    error: None,
                })
            }

            // ── 레거시 compat ──────────────────────────────
            HarnessStage::Init => {
                stages::init::execute(&self.config, context, &self.event_tx).await
            }
            HarnessStage::Execute => {
                stages::execute::execute(
                    &self.config, context, self.provider.as_ref(), &self.event_tx, &self.cancel_token,
                ).await
            }

            HarnessStage::Error => {
                Err(anyhow::anyhow!("Entered error state"))
            }
        }
    }

    /// 전이 결정 로직
    fn decide_transition(&self, stage: HarnessStage, result: &StageResult) -> StageTransition {
        match stage {
            // LLMCall → tool_calls 있으면 ToolExecute로, 없으면 Next
            HarnessStage::LLMCall => {
                let has_tool_calls = result.output["has_tool_calls"]
                    .as_bool()
                    .unwrap_or(false);
                if has_tool_calls && self.stages.contains(&HarnessStage::ToolExecute) {
                    StageTransition::JumpTo(HarnessStage::ToolExecute)
                } else {
                    // tool_calls 없음 = 최종 응답 → Next (Validate 또는 MemoryWrite 또는 Complete)
                    StageTransition::Next
                }
            }

            // ToolExecute 완료 → 도구를 실행했으면 LLMCall 복귀, 아니면 Next
            HarnessStage::ToolExecute => {
                let tools_executed = result.output["tools_executed"]
                    .as_u64()
                    .unwrap_or(0);
                if tools_executed > 0 {
                    StageTransition::JumpTo(HarnessStage::LLMCall)
                } else {
                    // 도구 0개 실행 = tool_calls가 없었음 → 루프 탈출
                    StageTransition::Next
                }
            }

            // Decide → 점수 미달이면 Plan으로 점프 (재시도)
            HarnessStage::Decide => {
                if let Some(score) = result.score {
                    if score < self.config.eval_threshold {
                        // Plan이 있으면 Plan으로, 없으면 LLMCall로 점프
                        let target = if self.stages.contains(&HarnessStage::Plan) {
                            HarnessStage::Plan
                        } else if self.stages.contains(&HarnessStage::LLMCall) {
                            HarnessStage::LLMCall
                        } else {
                            HarnessStage::Execute
                        };
                        return StageTransition::JumpTo(target);
                    }
                }
                StageTransition::Next
            }

            HarnessStage::Complete => {
                StageTransition::Complete(result.output.clone())
            }

            _ => StageTransition::Next,
        }
    }

    /// 복구 후 동작을 나타내는 enum
    async fn apply_recovery(
        &self,
        _stage: HarnessStage,
        error: &anyhow::Error,
        context: &mut ExecutionContext,
    ) -> RecoveryOutcome {
        use crate::stages::recover::RecoveryAction;

        let error_msg = error.to_string();
        let model = context.model_override.as_deref()
            .unwrap_or(&self.config.model);

        let action = context.recovery_manager.attempt_recovery(&error_msg, model);

        match action {
            RecoveryAction::Compact { .. } => {
                // 히스토리 압축: 최근 4개만 유지
                if context.messages.len() > 4 {
                    let keep = context.messages.split_off(context.messages.len() - 4);
                    context.messages = keep;
                    info!(agent_id = %self.agent_id, "Recovery: context compacted");
                }
                RecoveryOutcome::Retry
            }
            RecoveryAction::Escalate { new_max_tokens } => {
                context.max_tokens_override = Some(new_max_tokens);
                info!(agent_id = %self.agent_id, tokens = new_max_tokens, "Recovery: max_tokens escalated");
                RecoveryOutcome::Retry
            }
            RecoveryAction::Fallback { fallback_model, .. } => {
                context.model_override = Some(fallback_model.clone());
                info!(agent_id = %self.agent_id, model = %fallback_model, "Recovery: model fallback");
                RecoveryOutcome::Retry
            }
            RecoveryAction::Retry { hint } => {
                if let Some(h) = hint {
                    context.messages.push(serde_json::json!({
                        "role": "user",
                        "content": h
                    }));
                }
                info!(agent_id = %self.agent_id, "Recovery: simple retry");
                RecoveryOutcome::Retry
            }
            RecoveryAction::GiveUp { reason } => {
                warn!(agent_id = %self.agent_id, reason = %reason, "Recovery: giving up");
                RecoveryOutcome::GiveUp
            }
        }
    }

    async fn emit_stage_event(
        &self,
        event_type: &str,
        stage: HarnessStage,
        result: Option<&StageResult>,
    ) {
        // 파이프라인 내 현재 위치 (1-indexed)
        let step = self.stages.iter().position(|s| *s == stage).map(|i| i + 1);
        let total = self.stages.len();

        let data = serde_json::json!({
            "agent_id":   self.agent_id,
            "agent_name": self.agent_name,
            // 사용자 표시 ID (input/memory/system_prompt/plan/...)
            "stage_id":   stage.user_id(),
            // 사용자 표시명
            "stage":      stage.display_name(),
            "stage_ko":   stage.display_name_ko(),
            // 단계 설명 (로그 뷰어용)
            "description": stage.description_ko(),
            // 페이즈
            "phase":      stage.phase(),
            // 파이프라인 위치
            "step":       step,
            "total":      total,
            // 결과 데이터
            "score":      result.and_then(|r| r.score),
            "output":     result.map(|r| &r.output),
            "error":      result.and_then(|r| r.error.as_deref()),
        });

        let _ = self.event_tx.send(SseEvent {
            event: event_type.to_string(),
            data,
            id: None,
        });

        // tracing 로그에도 출력
        match event_type {
            "stage_enter" => tracing::info!(
                agent = %self.agent_name,
                stage = stage.display_name(),
                stage_ko = stage.display_name_ko(),
                step = step.unwrap_or(0),
                total,
                "[{}] {}  ▶ {}",
                stage.phase().to_uppercase(),
                stage.display_name(),
                stage.description_ko()
            ),
            "stage_exit" => tracing::info!(
                agent = %self.agent_name,
                stage = stage.display_name(),
                score = ?result.and_then(|r| r.score),
                "✓ {} 완료",
                stage.display_name(),
            ),
            "stage_error" => tracing::error!(
                agent = %self.agent_name,
                stage = stage.display_name(),
                "✗ {} 실패",
                stage.display_name(),
            ),
            _ => {}
        }
    }

    /// TraceSpan을 비동기로 DB 저장 (fire-and-forget)
    fn save_trace_span_async(
        &self,
        execution_log_id: i64,
        span_type: &str,
        name: &str,
        input: Option<serde_json::Value>,
        output: Option<serde_json::Value>,
        duration_ms: i64,
    ) {
        use crate::workflow::db::{DbManager, TraceSpan};

        let span = TraceSpan {
            execution_log_id,
            span_type: span_type.to_string(),
            name: name.to_string(),
            input,
            output,
            duration_ms,
            created_at: chrono::Utc::now(),
        };

        tokio::spawn(async move {
            let db_url = match std::env::var("DATABASE_URL") {
                Ok(url) => url,
                Err(_) => return,
            };
            let db = match DbManager::new(&db_url).await {
                Ok(db) => db,
                Err(_) => return,
            };
            if let Err(e) = db.save_trace_span(&span).await {
                tracing::warn!(error = %e, "TraceSpan 저장 실패");
            }
        });
    }

    /// 현재 stages 배열의 길이로 프리셋을 역추론
    fn current_preset_name(&self) -> String {
        match self.stages.len() {
            0..=4 => "minimal".to_string(),
            5..=7 => "standard".to_string(),
            8..=11 => "anthropic".to_string(),
            _ => "full".to_string(),
        }
    }

    /// tools 배열에서 MCP 세션을 파싱해서 McpClientManager 초기화
    ///
    /// 지원 URI 형식:
    /// - `mcp://session/SESSION_ID`  → xgen-mcp-station HTTP 트랜스포트
    /// - `mcp://bridge/nodes`        → Node MCP Bridge (Python subprocess, stdio)
    /// - `SESSION_ID` (plain)        → xgen-mcp-station HTTP 트랜스포트
    async fn init_mcp_clients(
        &self,
    ) -> Result<(Arc<tokio::sync::Mutex<McpClientManager>>, Arc<ToolRegistry>)> {
        let mcp_station_url = std::env::var("MCP_STATION_URL")
            .unwrap_or_else(|_| "http://xgen-mcp-station:8000".to_string());

        let mut mgr = McpClientManager::new();
        let mut registry = ToolRegistry::new();

        for tool_uri in &self.config.tools {
            if tool_uri == "mcp://bridge/nodes" || tool_uri.starts_with("mcp://bridge/nodes") {
                // Node MCP Bridge — Python 노드를 MCP 도구로 노출
                let nodes_dir = std::env::var("NODE_BRIDGE_NODES_DIR")
                    .unwrap_or_else(|_| "/app/workflow/editor/nodes".to_string());
                let bridge_script = std::env::var("NODE_BRIDGE_SCRIPT")
                    .unwrap_or_else(|_| "bridge/server.py".to_string());

                let categories_arg = if let Some(q) = tool_uri.strip_prefix("mcp://bridge/nodes?categories=") {
                    vec!["--categories".to_string(), q.to_string()]
                } else {
                    vec![]
                };

                let mut args = vec![bridge_script.clone(), "--nodes-dir".to_string(), nodes_dir.clone()];
                args.extend(categories_arg);

                let transport = McpTransport::Stdio {
                    command: "python3".to_string(),
                    args,
                    env: std::collections::HashMap::new(),
                };

                match mgr.add_server("node-bridge", transport).await {
                    Ok(()) => {
                        let discovered: Vec<_> = mgr.all_tools()
                            .into_iter()
                            .filter(|(srv, _)| srv == "node-bridge")
                            .map(|(_, t)| t.clone())
                            .collect();
                        info!(tools = discovered.len(), "Node MCP Bridge 연결 완료");
                        registry.register_server_tools("node-bridge", discovered);
                    }
                    Err(e) => {
                        warn!(error = %e, "Node MCP Bridge 연결 실패");
                    }
                }
                continue;
            }

            if tool_uri == "mcp://bridge/services" {
                // Service Tools Bridge — xgen-documents/mcp-station API를 MCP 도구로 래핑
                let bridge_script = std::env::var("SERVICE_BRIDGE_SCRIPT")
                    .unwrap_or_else(|_| "bridge/service_tools.py".to_string());

                let mut env_map = std::collections::HashMap::new();
                env_map.insert(
                    "DOCUMENTS_SERVICE_BASE_URL".to_string(),
                    std::env::var("DOCUMENTS_SERVICE_BASE_URL")
                        .unwrap_or_else(|_| "http://xgen-documents:8000".to_string()),
                );

                let transport = McpTransport::Stdio {
                    command: "python3".to_string(),
                    args: vec![bridge_script],
                    env: env_map,
                };

                match mgr.add_server("service-tools", transport).await {
                    Ok(()) => {
                        let discovered: Vec<_> = mgr.all_tools()
                            .into_iter()
                            .filter(|(srv, _)| srv == "service-tools")
                            .map(|(_, t)| t.clone())
                            .collect();
                        info!(tools = discovered.len(), "Service Tools Bridge 연결 완료 (문서검색, 컬렉션 등)");
                        registry.register_server_tools("service-tools", discovered);
                    }
                    Err(e) => {
                        warn!(error = %e, "Service Tools Bridge 연결 실패");
                    }
                }
                continue;
            }

            // URI 파싱: mcp://session/SESSION_ID 또는 plain SESSION_ID
            let session_id = if tool_uri.starts_with("mcp://session/") {
                tool_uri.trim_start_matches("mcp://session/").to_string()
            } else if tool_uri.starts_with("mcp://") {
                info!(uri = %tool_uri, "지원하지 않는 mcp:// URI 형식 — 스킵");
                continue;
            } else {
                // plain session ID
                tool_uri.clone()
            };

            let transport = McpTransport::Http {
                base_url: mcp_station_url.clone(),
                session_id: Some(session_id.clone()),
            };

            match mgr.add_server(&session_id, transport).await {
                Ok(()) => {
                    let discovered: Vec<_> = mgr.all_tools()
                        .into_iter()
                        .filter(|(srv, _)| srv == &session_id)
                        .map(|(_, t)| t.clone())
                        .collect();
                    info!(
                        session = %session_id,
                        tools = discovered.len(),
                        "MCP 세션 연결 완료"
                    );
                    registry.register_server_tools(&session_id, discovered);
                }
                Err(e) => {
                    warn!(session = %session_id, error = %e, "MCP 세션 연결 실패 — 스킵");
                }
            }
        }

        Ok((
            Arc::new(tokio::sync::Mutex::new(mgr)),
            Arc::new(registry),
        ))
    }
}

/// 실행 컨텍스트 — 단계 간 공유 상태
pub struct ExecutionContext {
    pub input: serde_json::Value,
    pub messages: Vec<serde_json::Value>,
    pub system_prompt: String,
    pub tool_results: Vec<serde_json::Value>,
    pub last_output: serde_json::Value,
    pub eval_score: Option<f64>,
    /// MCP 도구 매니저 (Phase 1)
    pub mcp_manager: Option<Arc<tokio::sync::Mutex<McpClientManager>>>,
    /// 도구 레지스트리 (Phase 1)
    pub tool_registry: Option<Arc<ToolRegistry>>,
    /// 에러 복구 매니저
    pub recovery_manager: crate::stages::recover::ErrorRecoveryManager,
    /// max_tokens 오버라이드 (에스컬레이션 시 설정)
    pub max_tokens_override: Option<u32>,
    /// 모델 오버라이드 (폴백 시 설정)
    pub model_override: Option<String>,
    /// RAG 컨텍스트
    pub rag_context: String,
    /// LLMCall 호출 횟수 (LLMCall↔ToolExecute 루프 추적용)
    pub llm_call_count: u32,
    /// 파이프라인에 ContextCompact 단계가 포함됐는지 여부 (LLMCall 자체 압축 스킵용)
    pub has_context_compact_stage: bool,
    /// 실행 시작 시각 (메트릭: duration_ms 계산용)
    pub start_time: std::time::Instant,
    /// 누적 입력 토큰 (모든 LLMCall 합산)
    pub total_input_tokens: u64,
    /// 누적 출력 토큰 (모든 LLMCall 합산)
    pub total_output_tokens: u64,
    /// DB 실행 로그 ID (MemoryWrite에서 설정, TraceSpan 저장용)
    pub execution_log_id: Option<i64>,
}
