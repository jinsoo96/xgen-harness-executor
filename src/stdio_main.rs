//! xgen-harness-stdio — stdin/stdout JSON-RPC CLI 바이너리
//!
//! Python에서 subprocess로 실행:
//!   1. stdin에 JSON-RPC 요청 한 줄 전송 후 닫기
//!   2. stdout에서 이벤트 알림 라인별 수신
//!   3. 마지막 줄이 JSON-RPC 응답 (result 또는 error)
//!
//! 로그는 stderr로 출력 (stdout은 프로토콜 전용).

use std::io::{self, BufRead, Write as IoWrite};

use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

use xgen_harness_executor::builder::HarnessBuilder;
use xgen_harness_executor::stdio::{
    HarnessRunParams, JsonRpcError, JsonRpcNotification, JsonRpcRequest, JsonRpcResponse,
};

fn main() {
    // 로깅 → stderr (stdout은 프로토콜 채널)
    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .with(tracing_subscriber::fmt::layer().with_writer(io::stderr))
        .init();

    let _ = dotenvy::dotenv();

    // stdin에서 요청 한 줄 읽기
    let stdin = io::stdin();
    let line = match stdin.lock().lines().next() {
        Some(Ok(l)) => l,
        Some(Err(e)) => {
            write_json_line(&JsonRpcError::new(
                serde_json::Value::Null,
                -32700,
                format!("stdin read error: {}", e),
            ));
            std::process::exit(1);
        }
        None => {
            write_json_line(&JsonRpcError::parse_error());
            std::process::exit(1);
        }
    };

    // JSON-RPC 파싱
    let request: JsonRpcRequest = match serde_json::from_str(&line) {
        Ok(r) => r,
        Err(e) => {
            write_json_line(&JsonRpcError::new(
                serde_json::Value::Null,
                -32700,
                format!("JSON parse error: {}", e),
            ));
            std::process::exit(1);
        }
    };

    let req_id = request.id.clone();

    // 메서드 확인
    if request.method != "harness/run" {
        write_json_line(&JsonRpcError::method_not_found(req_id));
        std::process::exit(1);
    }

    // 파라미터 파싱
    let params: HarnessRunParams = match serde_json::from_value(request.params) {
        Ok(p) => p,
        Err(e) => {
            write_json_line(&JsonRpcError::new(
                req_id,
                -32602,
                format!("Invalid params: {}", e),
            ));
            std::process::exit(1);
        }
    };

    info!(
        provider = %params.provider,
        model = %params.model,
        text_len = params.text.len(),
        "harness/run 요청 수신"
    );

    // tokio 런타임 생성 + 실행
    let rt = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            write_json_line(&JsonRpcError::execution_error(
                req_id,
                format!("runtime init failed: {}", e),
            ));
            std::process::exit(1);
        }
    };

    let result = rt.block_on(run_harness(params));

    match result {
        Ok(text) => {
            write_json_line(&JsonRpcResponse::ok(
                req_id,
                serde_json::json!({ "text": text }),
            ));
        }
        Err(e) => {
            write_json_line(&JsonRpcError::execution_error(
                req_id,
                e.to_string(),
            ));
            std::process::exit(1);
        }
    }
}

async fn run_harness(params: HarnessRunParams) -> anyhow::Result<String> {
    let mut builder = HarnessBuilder::new()
        .text(params.text)
        .provider(&params.provider, &params.model)
        .temperature(params.temperature)
        .max_tokens(params.max_tokens)
        .max_retries(params.max_retries)
        .eval_threshold(params.eval_threshold);

    if let Some(key) = params.api_key {
        builder = builder.api_key(key);
    }
    if let Some(prompt) = params.system_prompt {
        builder = builder.system_prompt(prompt);
    }
    if let Some(stages) = params.stages {
        builder = builder.stages(stages);
    }
    if let Some(tools) = params.tools {
        builder = builder.tools(tools);
    }

    // 이벤트 콜백에서 stdout 사용 (매번 lock/flush)
    let result = builder
        .run_with_events(move |event| {
            let notification = JsonRpcNotification::event(&event.event, event.data.clone());
            if let Ok(json) = serde_json::to_string(&notification) {
                let stdout = io::stdout();
                let mut handle = stdout.lock();
                let _ = writeln!(handle, "{}", json);
                let _ = handle.flush();
            }
        })
        .await?;

    Ok(result)
}

/// serde_json::Serialize를 stdout에 한 줄로 쓰고 flush
fn write_json_line(value: &impl serde::Serialize) {
    let stdout = io::stdout();
    let mut handle = stdout.lock();
    if let Ok(json) = serde_json::to_string(value) {
        let _ = writeln!(handle, "{}", json);
        let _ = handle.flush();
    }
}
