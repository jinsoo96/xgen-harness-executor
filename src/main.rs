use std::sync::Arc;

use axum::{
    routing::{get, post},
    Router,
};
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

use xgen_harness_executor::api::http::{AppConfig, AppState, execute_simple, execute_legacy, health};

#[tokio::main]
async fn main() {
    // 로깅 초기화
    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .with(tracing_subscriber::fmt::layer())
        .init();

    // 환경 변수 로드
    let _ = dotenvy::dotenv();

    let config = AppConfig::load().await;
    info!(
        mcp_station_url = %config.mcp_station_url,
        anthropic_key = !config.anthropic_api_key.is_empty(),
        openai_key = !config.openai_api_key.is_empty(),
        "Starting xgen-harness-executor"
    );

    // DB 마이그레이션은 xgen-core가 담당 (harness.py 모델 자동 등록)
    // Rust에서는 테이블이 이미 있다고 가정하고 사용만 함

    let state = AppState {
        config: Arc::new(config),
    };

    // CORS
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    // 라우터
    let app = Router::new()
        .route("/health", get(health))
        .route("/api/harness/health", get(health))
        .route("/api/harness/execute/simple", post(execute_simple))
        .route("/api/harness/execute/legacy", post(execute_legacy))
        .layer(TraceLayer::new_for_http())
        .layer(cors)
        .with_state(state);

    // 포트 설정
    let port = std::env::var("PORT").unwrap_or_else(|_| "8000".to_string());
    let addr = format!("0.0.0.0:{}", port);
    info!("Listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
