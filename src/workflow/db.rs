//! PostgreSQL 실행 로그 연동
//!
//! 기존 execution_io 테이블에 실행 로그를 기록.
//! sqlx를 사용한 비동기 DB 접근.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use tracing::{info, error};

/// 실행 로그 레코드
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionLog {
    pub workflow_id: String,
    pub interaction_id: String,
    pub user_id: i64,
    pub agent_id: String,
    pub agent_name: String,
    pub stage: String,
    pub input_data: serde_json::Value,
    pub output_data: serde_json::Value,
    pub status: String, // "started", "completed", "error"
    pub duration_ms: Option<i64>,
    pub token_usage: Option<serde_json::Value>,
    pub created_at: DateTime<Utc>,
}

/// 트레이스 스팬 레코드
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceSpan {
    pub execution_log_id: i64,
    pub span_type: String, // "llm_call", "tool_call", "stage_enter", etc
    pub name: String,
    pub input: Option<serde_json::Value>,
    pub output: Option<serde_json::Value>,
    pub duration_ms: i64,
    pub created_at: DateTime<Utc>,
}

/// DB 매니저 — PostgreSQL 실행 로그 관리
pub struct DbManager {
    pool: Option<sqlx::PgPool>,
}

impl DbManager {
    /// DB 풀 없이 생성 (Phase 4에서 연결)
    pub fn new_without_pool() -> Self {
        Self { pool: None }
    }

    /// DB 풀과 함께 생성
    pub async fn new(database_url: &str) -> Result<Self, sqlx::Error> {
        let pool = sqlx::PgPool::connect(database_url).await?;
        info!("Database connected");
        Ok(Self { pool: Some(pool) })
    }

    /// 실행 로그 저장
    pub async fn save_execution_log(&self, log: &ExecutionLog) -> Result<i64, String> {
        let pool = match &self.pool {
            Some(p) => p,
            None => return Err("No database connection".to_string()),
        };

        let row = sqlx::query(
            r#"
            INSERT INTO harness_execution_log
                (workflow_id, interaction_id, user_id, agent_id, agent_name,
                 stage, input_data, output_data, status, duration_ms, token_usage, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
            "#,
        )
        .bind(&log.workflow_id)
        .bind(&log.interaction_id)
        .bind(log.user_id)
        .bind(&log.agent_id)
        .bind(&log.agent_name)
        .bind(&log.stage)
        .bind(&log.input_data)
        .bind(&log.output_data)
        .bind(&log.status)
        .bind(log.duration_ms)
        .bind(&log.token_usage)
        .bind(log.created_at)
        .fetch_one(pool)
        .await;

        match row {
            Ok(row) => {
                use sqlx::Row;
                let id: i64 = row.get("id");
                Ok(id)
            }
            Err(e) => {
                error!(error = %e, "Failed to save execution log");
                Err(e.to_string())
            }
        }
    }

    /// workflow_id 기준 최근 완료 실행 결과 텍스트 반환 (메모리 프리페치용)
    pub async fn fetch_recent_executions(
        &self,
        workflow_id: &str,
        limit: i64,
    ) -> Vec<String> {
        let pool = match &self.pool {
            Some(p) => p,
            None => return vec![],
        };

        let rows = sqlx::query(
            r#"
            SELECT output_data
            FROM harness_execution_log
            WHERE workflow_id = $1
              AND status = 'completed'
            ORDER BY created_at DESC
            LIMIT $2
            "#,
        )
        .bind(workflow_id)
        .bind(limit)
        .fetch_all(pool)
        .await;

        match rows {
            Ok(rows) => {
                use sqlx::Row;
                rows.iter()
                    .filter_map(|r| {
                        let val: serde_json::Value = r.try_get("output_data").ok()?;
                        // output_data.content 또는 output_data 자체가 문자열인 경우
                        val.get("content")
                            .and_then(|v| v.as_str())
                            .or_else(|| val.as_str())
                            .map(String::from)
                            .filter(|s| !s.is_empty())
                    })
                    .collect()
            }
            Err(e) => {
                tracing::warn!(error = %e, workflow_id, "이전 실행 결과 조회 실패");
                vec![]
            }
        }
    }

    /// 트레이스 스팬 저장
    pub async fn save_trace_span(&self, span: &TraceSpan) -> Result<i64, String> {
        let pool = match &self.pool {
            Some(p) => p,
            None => return Err("No database connection".to_string()),
        };

        let row = sqlx::query(
            r#"
            INSERT INTO harness_trace_span
                (execution_log_id, span_type, name, input, output, duration_ms, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            "#,
        )
        .bind(span.execution_log_id)
        .bind(&span.span_type)
        .bind(&span.name)
        .bind(&span.input)
        .bind(&span.output)
        .bind(span.duration_ms)
        .bind(span.created_at)
        .fetch_one(pool)
        .await;

        match row {
            Ok(row) => {
                use sqlx::Row;
                let id: i64 = row.get("id");
                Ok(id)
            }
            Err(e) => {
                error!(error = %e, "Failed to save trace span");
                Err(e.to_string())
            }
        }
    }
}

/// DB 마이그레이션 SQL
pub const MIGRATION_SQL: &str = r#"
-- harness-executor 실행 로그 테이블
CREATE TABLE IF NOT EXISTS harness_execution_log (
    id BIGSERIAL PRIMARY KEY,
    workflow_id VARCHAR(255) NOT NULL,
    interaction_id VARCHAR(255) NOT NULL,
    user_id BIGINT NOT NULL,
    agent_id VARCHAR(255) NOT NULL,
    agent_name VARCHAR(255) NOT NULL,
    stage VARCHAR(50) NOT NULL,
    input_data JSONB DEFAULT '{}',
    output_data JSONB DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'started',
    duration_ms BIGINT,
    token_usage JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_harness_exec_workflow ON harness_execution_log(workflow_id);
CREATE INDEX IF NOT EXISTS idx_harness_exec_interaction ON harness_execution_log(interaction_id);
CREATE INDEX IF NOT EXISTS idx_harness_exec_user ON harness_execution_log(user_id);

-- harness-executor 트레이스 스팬 테이블
CREATE TABLE IF NOT EXISTS harness_trace_span (
    id BIGSERIAL PRIMARY KEY,
    execution_log_id BIGINT NOT NULL REFERENCES harness_execution_log(id),
    span_type VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    input JSONB,
    output JSONB,
    duration_ms BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_harness_trace_exec ON harness_trace_span(execution_log_id);
"#;
