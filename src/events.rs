//! SSE 이벤트 타입 정의
//!
//! core feature에 포함 — 라이브러리/서버 양쪽에서 공용.

use serde::{Deserialize, Serialize};

/// SSE 이벤트 구조체
/// 기존 xgen-workflow SSE 포맷과 호환 + 하네스 전용 이벤트 추가
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SseEvent {
    /// 이벤트 타입: message, tool_call, tool_result, stage_enter, stage_exit,
    /// evaluation, decision, error, log, node_status
    pub event: String,
    /// 이벤트 데이터 (JSON)
    pub data: serde_json::Value,
    /// 이벤트 ID (선택)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
}

impl SseEvent {
    /// SSE 텍스트 포맷으로 변환
    /// `event: {type}\ndata: {json}\n\n`
    pub fn to_sse_string(&self) -> String {
        let data_str = serde_json::to_string(&self.data).unwrap_or_default();
        let mut result = String::new();

        if let Some(ref id) = self.id {
            result.push_str(&format!("id: {}\n", id));
        }

        result.push_str(&format!("event: {}\n", self.event));
        result.push_str(&format!("data: {}\n\n", data_str));
        result
    }

    /// 텍스트 메시지 이벤트
    pub fn text(text: &str) -> Self {
        Self {
            event: "message".to_string(),
            data: serde_json::json!({"type": "text", "text": text}),
            id: None,
        }
    }

    /// 에러 이벤트
    pub fn error(msg: &str) -> Self {
        Self {
            event: "error".to_string(),
            data: serde_json::json!({"message": msg}),
            id: None,
        }
    }

    /// 실행 완료 이벤트
    pub fn done(result: serde_json::Value) -> Self {
        Self {
            event: "done".to_string(),
            data: result,
            id: None,
        }
    }
}
