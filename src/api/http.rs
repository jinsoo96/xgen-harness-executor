//! HTTP API 핸들러
//!
//! REST 엔드포인트:
//!   GET  /health                          — 헬스체크
//!   POST /api/harness/execute/simple      — 단일 에이전트 실행 (SSE 스트리밍)
//!   POST /api/harness/execute/legacy      — 기존 workflow JSON → 자동 변환 후 실행 (SSE 스트리밍)

use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;
use tokio_stream::wrappers::UnboundedReceiverStream;
use tokio_util::sync::CancellationToken;
use tracing::{error, info, warn};
use uuid::Uuid;

use crate::events::SseEvent;
use crate::llm::provider::create_provider;
use crate::state_machine::agent_executor::{AgentConfig, AgentStateMachine};
use crate::state_machine::orchestrator::OrchestrationPattern;
use crate::state_machine::stage::HarnessStage;
use crate::workflow::converter::{convert_legacy_to_harness, LegacyWorkflow};

/// 공유 앱 상태
#[derive(Clone)]
pub struct AppState {
    pub config: Arc<AppConfig>,
}

/// 앱 설정
#[derive(Debug, Clone)]
pub struct AppConfig {
    pub anthropic_api_key: String,
    pub openai_api_key: String,
    pub mcp_station_url: String,
    pub xgen_core_url: String,
}

impl AppConfig {
    /// 환경변수에서 로드, 없으면 xgen-core에서 fetch
    pub async fn load() -> Self {
        let xgen_core_url = std::env::var("XGEN_CORE_URL")
            .unwrap_or_else(|_| "http://xgen-core:8000".to_string());

        let anthropic = fetch_api_key("ANTHROPIC_API_KEY", &xgen_core_url).await;
        let openai = fetch_api_key("OPENAI_API_KEY", &xgen_core_url).await;

        info!(
            anthropic = !anthropic.is_empty(),
            openai = !openai.is_empty(),
            "API keys loaded"
        );

        Self {
            anthropic_api_key: anthropic,
            openai_api_key: openai,
            mcp_station_url: std::env::var("MCP_STATION_URL")
                .unwrap_or_else(|_| "http://xgen-mcp-station:8000".to_string()),
            xgen_core_url,
        }
    }
}

/// 환경변수 → 없으면 xgen-core에서 fetch (최대 3회 재시도, core 부팅 대기)
async fn fetch_api_key(name: &str, xgen_core_url: &str) -> String {
    // 1차: 환경변수
    if let Ok(val) = std::env::var(name) {
        if !val.is_empty() {
            info!(key = %name, source = "env", "API key loaded from environment");
            return val;
        }
    }

    // 2차: xgen-core API (재시도 3회, 2초 간격 — core 부팅 대기)
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .unwrap_or_default();

    for attempt in 1..=3 {
        match client
            .post(format!("{}/api/data/config/get-value", xgen_core_url))
            .json(&serde_json::json!({"env_name": name, "default": ""}))
            .send()
            .await
        {
            Ok(resp) => {
                if let Ok(body) = resp.json::<serde_json::Value>().await {
                    let val = body["value"].as_str().unwrap_or("").to_string();
                    if !val.is_empty() {
                        info!(key = %name, source = "xgen-core", "API key fetched from xgen-core");
                        return val;
                    }
                }
                // 값이 비어있으면 아직 설정 안 된 것 — 재시도 의미 없음
                break;
            }
            Err(e) => {
                if attempt < 3 {
                    warn!(
                        key = %name, attempt, error = %e,
                        "xgen-core 연결 실패, {}초 후 재시도", attempt * 2
                    );
                    tokio::time::sleep(std::time::Duration::from_secs(attempt as u64 * 2)).await;
                } else {
                    warn!(key = %name, error = %e, "xgen-core에서 API key 가져오기 실패 (3회 재시도 완료)");
                }
            }
        }
    }

    String::new()
}

/// 첨부 파일 (Python이 텍스트 추출 / base64 인코딩 후 전달)
#[derive(Debug, Deserialize, Clone)]
pub struct AttachedFile {
    pub name: String,
    /// 텍스트 파일: 추출된 텍스트 / 이미지: base64 인코딩 데이터
    pub content: String,
    pub file_type: String,
    pub is_image: bool,
}

/// GET /health
pub async fn health() -> impl IntoResponse {
    Json(serde_json::json!({
        "status": "ok",
        "service": "xgen-harness-executor",
        "version": "0.1.0"
    }))
}

// ---------------------------------------------------------------------------
// /api/harness/execute/simple
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct SimpleExecuteRequest {
    pub text: String,
    #[serde(default = "default_provider")]
    pub provider: String,
    #[serde(default = "default_model")]
    pub model: String,
    #[serde(default = "default_stages")]
    pub stages: Vec<HarnessStage>,
    #[serde(default)]
    pub tools: Vec<String>,
    pub system_prompt: Option<String>,
}

fn default_provider() -> String { "anthropic".to_string() }
fn default_model() -> String { "claude-sonnet-4-6".to_string() }
fn default_stages() -> Vec<HarnessStage> {
    vec![HarnessStage::Init, HarnessStage::Execute, HarnessStage::Complete]
}

#[derive(Debug, Serialize)]
pub struct ExecuteResponse {
    pub execution_id: String,
    pub output: serde_json::Value,
}

pub async fn execute_simple(
    State(state): State<AppState>,
    Json(req): Json<SimpleExecuteRequest>,
) -> Response {
    let execution_id = Uuid::new_v4().to_string();
    info!(
        execution_id = %execution_id,
        provider = %req.provider,
        model = %req.model,
        tools = req.tools.len(),
        "execute/simple"
    );

    let provider = match build_provider(&state.config, &req.provider) {
        Ok(p) => p,
        Err(e) => return error_response(e),
    };

    let (event_tx, event_rx) = mpsc::unbounded_channel::<SseEvent>();
    let cancel_token = CancellationToken::new();

    let config = AgentConfig {
        provider_name: req.provider.clone(),
        model: req.model.clone(),
        system_prompt: req.system_prompt.unwrap_or_default(),
        temperature: 0.7,
        max_tokens: 8192,
        max_retries: 3,
        eval_threshold: 0.7,
        context_budget: 200_000,
        tools: req.tools,
        modules: vec![],
    };

    let exec_id = execution_id.clone();
    let input_text = req.text.clone();

    tokio::spawn(async move {
        let mut machine = AgentStateMachine::new(
            exec_id.clone(),
            "simple-agent".to_string(),
            req.stages,
            config,
            provider,
            event_tx.clone(),
            cancel_token,
        );

        let input = serde_json::json!({
            "text": input_text,
            "execution_id": exec_id,
            "image_blocks": serde_json::Value::Null,
        });

        match machine.run(input).await {
            Ok(output) => {
                let _ = event_tx.send(SseEvent {
                    event: "done".to_string(),
                    data: serde_json::json!({"execution_id": exec_id, "output": output}),
                    id: None,
                });
            }
            Err(e) => {
                error!(execution_id = %exec_id, error = %e, "execution failed");
                let _ = event_tx.send(SseEvent {
                    event: "error".to_string(),
                    data: serde_json::json!({"message": e.to_string()}),
                    id: None,
                });
            }
        }
    });

    sse_response(event_rx)
}

// ---------------------------------------------------------------------------
// /api/harness/execute/legacy
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct LegacyExecuteRequest {
    /// 사용자 입력 텍스트
    pub text: String,
    /// React Flow 캔버스 JSON (nodes + edges)
    pub workflow_data: serde_json::Value,
    #[serde(default)]
    pub workflow_id: String,
    #[serde(default)]
    pub workflow_name: String,
    /// interaction_id (Python 측 세션 ID → execution_id로 사용)
    #[serde(default)]
    pub interaction_id: String,
    /// 사용자 ID (Python execution_core.py가 인증 후 전달)
    #[serde(default)]
    pub user_id: Option<String>,
    /// 첨부 파일 (Python에서 텍스트 추출/base64 완료 후 전달)
    #[serde(default)]
    pub attached_files: Option<Vec<AttachedFile>>,
    /// 이전 실행 결과 (Python execution_core.py가 DB 조회 후 주입)
    #[serde(default)]
    pub previous_results: Option<Vec<String>>,
}

pub async fn execute_legacy(
    State(state): State<AppState>,
    Json(req): Json<LegacyExecuteRequest>,
) -> Response {
    let execution_id = Uuid::new_v4().to_string();
    info!(
        execution_id = %execution_id,
        workflow_id = %req.workflow_id,
        workflow_name = %req.workflow_name,
        "execute/legacy"
    );

    // 1. React Flow JSON → LegacyWorkflow → harness-v1 변환
    let legacy = match LegacyWorkflow::from_raw_value(&req.workflow_data) {
        Ok(l) => l,
        Err(e) => {
            error!(error = %e, "Failed to parse legacy workflow");
            return error_response(anyhow::anyhow!("Invalid workflow_data: {}", e));
        }
    };

    let harness_wf = match convert_legacy_to_harness(&legacy, &req.workflow_id, &req.workflow_name) {
        Ok(w) => w,
        Err(e) => {
            error!(error = %e, "Failed to convert workflow");
            return error_response(anyhow::anyhow!("Conversion failed: {}", e));
        }
    };

    info!(
        agents = harness_wf.agents.len(),
        orchestration = ?harness_wf.orchestration,
        "Converted to harness-v1"
    );

    // 2. 에이전트 정의 목록
    let agent_defs = harness_wf.to_agent_definitions();
    if agent_defs.is_empty() {
        return error_response(anyhow::anyhow!("No agents in workflow"));
    }

    // 3. 첨부 파일 → 초기 입력 메시지 구성
    //    - 텍스트/문서: text 뒤에 [첨부 파일: name]\ncontent 형태로 추가
    //    - 이미지: image_blocks 배열로 분리 → init.rs에서 MessageContent::Blocks 구성
    let (augmented_text, image_blocks) = build_input_with_files(&req.text, req.attached_files.as_deref());

    // 4. SSE 채널 (전체 공유)
    let (event_tx, event_rx) = mpsc::unbounded_channel::<SseEvent>();
    let orchestration = harness_wf.orchestration.clone();
    let config = Arc::clone(&state.config);
    let exec_id = execution_id.clone();

    // 초기 입력 JSON — previous_results, user_id는 Python(execution_core.py)이 채워서 넘김
    let previous_results = req.previous_results.unwrap_or_default();
    let user_id: i64 = req.user_id
        .as_deref()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    // interaction_id가 있으면 execution_id로 사용 (DB 저장 연속성)
    let execution_id_for_input = if !req.interaction_id.is_empty() {
        req.interaction_id.clone()
    } else {
        exec_id.clone()
    };
    let initial_input = serde_json::json!({
        "text": augmented_text,
        "execution_id": execution_id_for_input,
        "workflow_id": req.workflow_id,
        "workflow_name": req.workflow_name,
        "image_blocks": image_blocks,
        "previous_results": previous_results,
        "user_id": user_id,
    });

    tokio::spawn(async move {
        let result = match orchestration {
            OrchestrationPattern::Parallel => {
                run_parallel(agent_defs, initial_input, &config, event_tx.clone(), exec_id.clone()).await
            }
            OrchestrationPattern::Pipeline => {
                run_pipeline(agent_defs, initial_input, &config, event_tx.clone(), exec_id.clone()).await
            }
            OrchestrationPattern::Supervisor { ref lead } => {
                run_supervisor(agent_defs, initial_input, &config, event_tx.clone(), exec_id.clone(), lead.clone()).await
            }
            OrchestrationPattern::Sequential => {
                run_sequential(agent_defs, initial_input, &config, event_tx.clone(), exec_id.clone()).await
            }
        };

        match result {
            Ok(final_output) => {
                let _ = event_tx.send(SseEvent {
                    event: "done".to_string(),
                    data: serde_json::json!({"execution_id": exec_id, "output": final_output}),
                    id: None,
                });
            }
            Err(e) => {
                error!(execution_id = %exec_id, error = %e, "Legacy execution failed");
                let _ = event_tx.send(SseEvent {
                    event: "error".to_string(),
                    data: serde_json::json!({"message": e.to_string()}),
                    id: None,
                });
            }
        }
    });

    sse_response(event_rx)
}

// ---------------------------------------------------------------------------
// 오케스트레이션 실행 헬퍼
// ---------------------------------------------------------------------------

async fn run_sequential(
    agent_defs: Vec<crate::state_machine::orchestrator::AgentDefinition>,
    initial_input: serde_json::Value,
    config: &AppConfig,
    event_tx: mpsc::UnboundedSender<SseEvent>,
    exec_id: String,
) -> anyhow::Result<serde_json::Value> {
    let mut current_input = initial_input;

    for (i, def) in agent_defs.iter().enumerate() {
        let provider = build_provider(config, &def.provider)?;
        let cancel_token = CancellationToken::new();

        let agent_id = format!("{}-{}", exec_id, i);
        let mut machine = AgentStateMachine::new(
            agent_id,
            def.name.clone(),
            def.stages.clone(),
            def.config.clone(),
            provider,
            event_tx.clone(),
            cancel_token,
        );

        current_input = machine.run(current_input).await?;
    }

    Ok(current_input)
}

async fn run_parallel(
    agent_defs: Vec<crate::state_machine::orchestrator::AgentDefinition>,
    initial_input: serde_json::Value,
    config: &AppConfig,
    event_tx: mpsc::UnboundedSender<SseEvent>,
    exec_id: String,
) -> anyhow::Result<serde_json::Value> {
    let mut handles = Vec::new();

    for (i, def) in agent_defs.into_iter().enumerate() {
        let provider = build_provider(config, &def.provider)?;
        let cancel_token = CancellationToken::new();
        let tx = event_tx.clone();
        let input = initial_input.clone();
        let agent_id = format!("{}-{}", exec_id, i);

        let handle = tokio::spawn(async move {
            let mut machine = AgentStateMachine::new(
                agent_id,
                def.name.clone(),
                def.stages.clone(),
                def.config.clone(),
                provider,
                tx,
                cancel_token,
            );
            machine.run(input).await
        });

        handles.push(handle);
    }

    let mut outputs = Vec::new();
    for h in handles {
        outputs.push(h.await??);
    }

    Ok(serde_json::json!({"outputs": outputs}))
}

/// Pipeline — 앞 에이전트의 출력 텍스트를 다음 에이전트의 입력 텍스트에 주입
async fn run_pipeline(
    agent_defs: Vec<crate::state_machine::orchestrator::AgentDefinition>,
    initial_input: serde_json::Value,
    config: &AppConfig,
    event_tx: mpsc::UnboundedSender<SseEvent>,
    exec_id: String,
) -> anyhow::Result<serde_json::Value> {
    let mut current_input = initial_input;

    for (i, def) in agent_defs.iter().enumerate() {
        let provider = build_provider(config, &def.provider)?;
        let cancel_token = CancellationToken::new();

        let agent_id = format!("{}-{}", exec_id, i);
        let mut machine = AgentStateMachine::new(
            agent_id,
            def.name.clone(),
            def.stages.clone(),
            def.config.clone(),
            provider,
            event_tx.clone(),
            cancel_token,
        );

        let output = machine.run(current_input.clone()).await?;

        // 다음 에이전트에게는 이전 출력의 텍스트를 입력 텍스트에 주입
        if i < agent_defs.len() - 1 {
            let prev_text = output
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let orig_text = current_input
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("");

            let chained_text = format!(
                "{}\n\n--- 이전 에이전트({}) 결과 ---\n{}",
                orig_text,
                def.name,
                prev_text
            );

            if let Some(obj) = current_input.as_object_mut() {
                obj.insert("text".to_string(), serde_json::Value::String(chained_text));
            }
        } else {
            current_input = output;
        }
    }

    Ok(current_input)
}

/// Supervisor — lead 에이전트가 다른 에이전트들을 도구로 호출
///
/// 구현 방식:
/// 1. worker 에이전트들을 실행 대기 상태로 준비
/// 2. lead 에이전트의 system_prompt에 worker 목록 + 호출 방법 주입
/// 3. lead 에이전트의 도구 목록에 `delegate_to_agent` 도구 추가
/// 4. lead가 delegate_to_agent 도구를 호출하면 해당 worker 실행 후 결과 반환
///
/// 현재 단순화된 구현: lead가 먼저 실행되고, lead 결과에 worker 참조가 있으면 해당 worker 실행.
/// TODO: 진정한 MCP 기반 delegate는 Phase 11에서 구현.
async fn run_supervisor(
    agent_defs: Vec<crate::state_machine::orchestrator::AgentDefinition>,
    initial_input: serde_json::Value,
    config: &AppConfig,
    event_tx: mpsc::UnboundedSender<SseEvent>,
    exec_id: String,
    lead_id: String,
) -> anyhow::Result<serde_json::Value> {
    // lead 에이전트와 worker 분리
    let (lead_idx, _) = agent_defs
        .iter()
        .enumerate()
        .find(|(_, d)| d.id == lead_id || d.name == lead_id)
        .unwrap_or((0, &agent_defs[0]));

    let mut lead_def = agent_defs[lead_idx].clone();
    let workers: Vec<_> = agent_defs
        .iter()
        .enumerate()
        .filter(|(i, _)| *i != lead_idx)
        .map(|(_, d)| d.clone())
        .collect();

    // lead의 system_prompt에 worker 정보 주입
    let worker_info: Vec<String> = workers
        .iter()
        .enumerate()
        .map(|(i, w)| {
            format!(
                "  {}. **{}** (provider: {}, model: {}) — 이 에이전트에게 작업을 위임할 수 있습니다.",
                i + 1, w.name, w.provider, w.model
            )
        })
        .collect();

    let supervisor_instruction = format!(
        "\n\n## Supervisor 모드\n\
         당신은 Supervisor 에이전트입니다. 아래 Worker 에이전트들에게 작업을 위임할 수 있습니다.\n\
         작업을 위임하려면 응답에 [DELEGATE:에이전트이름] 태그를 사용하세요.\n\n\
         ### Worker 에이전트 목록:\n{}\n\n\
         복잡한 작업은 Worker에게 위임하고, 최종 결과를 종합하여 응답하세요.",
        worker_info.join("\n")
    );

    lead_def.config.system_prompt.push_str(&supervisor_instruction);

    // 1단계: lead 에이전트 실행
    let provider = build_provider(config, &lead_def.provider)?;
    let cancel_token = CancellationToken::new();
    let agent_id = format!("{}-lead", exec_id);

    let mut machine = AgentStateMachine::new(
        agent_id,
        lead_def.name.clone(),
        lead_def.stages.clone(),
        lead_def.config.clone(),
        provider,
        event_tx.clone(),
        cancel_token,
    );

    let lead_output = machine.run(initial_input.clone()).await?;
    let lead_text = lead_output
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // 2단계: lead 결과에서 [DELEGATE:이름] 패턴을 찾아 worker 실행
    let mut final_output = lead_output.clone();

    for worker in &workers {
        let delegate_tag = format!("[DELEGATE:{}]", worker.name);
        if lead_text.contains(&delegate_tag) {
            info!(
                lead = %lead_def.name,
                worker = %worker.name,
                "Supervisor: delegating to worker"
            );

            let _ = event_tx.send(SseEvent {
                event: "debug_log".to_string(),
                data: serde_json::json!({
                    "message": format!("Supervisor: {} → {} 위임", lead_def.name, worker.name)
                }),
                id: None,
            });

            let w_provider = build_provider(config, &worker.provider)?;
            let w_cancel = CancellationToken::new();
            let w_agent_id = format!("{}-worker-{}", exec_id, worker.name);

            // worker의 입력에 lead의 지시 텍스트 포함
            let mut w_input = initial_input.clone();
            if let Some(obj) = w_input.as_object_mut() {
                let worker_text = format!(
                    "{}\n\n--- Supervisor({}) 지시 ---\n{}",
                    obj.get("text").and_then(|v| v.as_str()).unwrap_or(""),
                    lead_def.name,
                    lead_text
                );
                obj.insert("text".to_string(), serde_json::Value::String(worker_text));
            }

            let mut w_machine = AgentStateMachine::new(
                w_agent_id,
                worker.name.clone(),
                worker.stages.clone(),
                worker.config.clone(),
                w_provider,
                event_tx.clone(),
                w_cancel,
            );

            let worker_output = w_machine.run(w_input).await?;
            let worker_text = worker_output
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("");

            // 최종 결과에 worker 결과 병합
            let combined = format!(
                "{}\n\n--- Worker({}) 결과 ---\n{}",
                lead_text, worker.name, worker_text
            );
            final_output = serde_json::json!({
                "text": combined,
                "lead_output": lead_output,
                "worker_outputs": {worker.name.clone(): worker_output},
            });
        }
    }

    Ok(final_output)
}

// ---------------------------------------------------------------------------
// 공통 헬퍼
// ---------------------------------------------------------------------------

/// 첨부 파일 → (augmented_text, image_blocks)
///
/// - 텍스트/문서: content를 text 뒤에 붙임
/// - 이미지: Anthropic Vision 형식의 ContentBlock JSON 배열로 분리
fn build_input_with_files(
    text: &str,
    files: Option<&[AttachedFile]>,
) -> (String, serde_json::Value) {
    let Some(files) = files else {
        return (text.to_string(), serde_json::Value::Null);
    };

    let mut augmented = text.to_string();
    let mut image_blocks: Vec<serde_json::Value> = vec![];

    for file in files {
        if file.is_image {
            image_blocks.push(serde_json::json!({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": file.file_type,
                    "data": file.content,
                }
            }));
        } else if !file.content.is_empty() {
            augmented.push_str(&format!(
                "\n\n[첨부 파일: {}]\n{}",
                file.name, file.content
            ));
        }
    }

    let blocks_val = if image_blocks.is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::Value::Array(image_blocks)
    };

    (augmented, blocks_val)
}

fn build_provider(
    config: &AppConfig,
    provider_name: &str,
) -> anyhow::Result<Arc<dyn crate::llm::provider::LlmProvider>> {
    match provider_name {
        "anthropic" => {
            if config.anthropic_api_key.is_empty() {
                return Err(anyhow::anyhow!("ANTHROPIC_API_KEY not available"));
            }
            Ok(Arc::from(create_provider("anthropic", &config.anthropic_api_key, None)?))
        }
        "openai" => {
            if config.openai_api_key.is_empty() {
                return Err(anyhow::anyhow!("OPENAI_API_KEY not available"));
            }
            Ok(Arc::from(create_provider("openai", &config.openai_api_key, None)?))
        }
        unknown => Err(anyhow::anyhow!("Unknown provider: {}", unknown)),
    }
}

fn error_response(e: anyhow::Error) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({"error": e.to_string()})),
    )
        .into_response()
}

fn sse_response(event_rx: mpsc::UnboundedReceiver<SseEvent>) -> Response {
    use axum::body::Body;
    use axum::http::header;

    let stream = UnboundedReceiverStream::new(event_rx).map(|event| {
        Ok::<_, std::convert::Infallible>(axum::body::Bytes::from(event.to_sse_string()))
    });

    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header(header::CONNECTION, "keep-alive")
        .header("X-Accel-Buffering", "no")
        .body(Body::from_stream(stream))
        .unwrap()
}
