//! Error Recovery — Python error_recovery.py (218줄) 포팅
//!
//! Claude Code query.ts의 7가지 continue 경로 포팅.
//! 각 복구 경로는 상태를 변경하고 다음 루프 이터레이션에서 재시도.

use tracing::{info, warn};

/// 에러 유형 감지 패턴
const PROMPT_TOO_LONG_PATTERNS: &[&str] = &[
    "prompt is too long",
    "context_length_exceeded",
    "maximum context length",
    "token limit exceeded",
    "request too large",
    "413",
];

const MAX_OUTPUT_PATTERNS: &[&str] = &[
    "max_tokens",
    "maximum.*output.*tokens",
    "length.*limit",
    "output.*truncated",
];

/// Max output tokens 에스컬레이션 (원본: 8k → 64k)
pub const ESCALATED_MAX_TOKENS: u32 = 65_536;
pub const DEFAULT_MAX_TOKENS: u32 = 8_192;

/// 모델 fallback 매핑
fn fallback_model(model: &str) -> Option<&'static str> {
    match model {
        "gpt-4.1" => Some("gpt-4.1-mini"),
        "gpt-4o" => Some("gpt-4o-mini"),
        "gpt-5" => Some("gpt-4.1"),
        "claude-sonnet-4-5-20250929" => Some("claude-haiku-4-5-20251001"),
        "claude-opus-4-6" => Some("claude-sonnet-4-5-20250929"),
        _ => None,
    }
}

/// 에러 유형
#[derive(Debug, Clone, PartialEq)]
pub enum ErrorType {
    PromptTooLong,
    MaxOutputTokens,
    RateLimit,
    Timeout,
    Unknown,
}

/// 에러 메시지에서 복구 가능한 에러 유형 감지
pub fn detect_error_type(error_msg: &str) -> ErrorType {
    let lower = error_msg.to_lowercase();

    for pattern in PROMPT_TOO_LONG_PATTERNS {
        if lower.contains(&pattern.to_lowercase()) {
            return ErrorType::PromptTooLong;
        }
    }

    for pattern in MAX_OUTPUT_PATTERNS {
        if lower.contains(&pattern.to_lowercase()) {
            return ErrorType::MaxOutputTokens;
        }
    }

    if lower.contains("rate") && lower.contains("limit") {
        return ErrorType::RateLimit;
    }

    if lower.contains("timeout") || lower.contains("timed out") {
        return ErrorType::Timeout;
    }

    ErrorType::Unknown
}

/// 복구 액션
#[derive(Debug, Clone)]
pub enum RecoveryAction {
    /// 컨텍스트 압축 후 재시도
    Compact { strategy: String },
    /// max_tokens 에스컬레이션
    Escalate { new_max_tokens: u32 },
    /// 모델 폴백
    Fallback {
        original_model: String,
        fallback_model: String,
    },
    /// 단순 재시도
    Retry { hint: Option<String> },
    /// 복구 불가
    GiveUp { reason: String },
}

/// 에러 복구 상태 추적
/// OpenClaude query.ts의 state 중 복구 관련 필드 포팅.
pub struct RecoveryState {
    /// 413 복구 시도 여부
    pub has_attempted_reactive_compact: bool,
    /// Max output tokens 오버라이드
    pub max_output_tokens_override: Option<u32>,
    /// Max output 복구 횟수
    pub max_output_tokens_recovery_count: u32,
    /// 모델 폴백 시도 여부
    pub fallback_attempted: bool,
    /// 원래 모델
    pub original_model: Option<String>,
    /// 연속 실패 횟수
    pub consecutive_failures: u32,
}

/// 원본 상수
const MAX_OUTPUT_RECOVERY_LIMIT: u32 = 3;
const MAX_CONSECUTIVE_FAILURES: u32 = 3;

impl RecoveryState {
    pub fn new() -> Self {
        Self {
            has_attempted_reactive_compact: false,
            max_output_tokens_override: None,
            max_output_tokens_recovery_count: 0,
            fallback_attempted: false,
            original_model: None,
            consecutive_failures: 0,
        }
    }

    /// 성공 시 복구 상태 초기화
    pub fn reset_on_success(&mut self) {
        self.consecutive_failures = 0;
        self.max_output_tokens_recovery_count = 0;
    }
}

/// Error Recovery Manager
/// Claude Code query.ts의 7가지 error recovery 경로 포팅.
///
/// 1. collapse_drain_retry — 413 시 context collapse 드레인
/// 2. reactive_compact_retry — 413/미디어 에러 시 전체 compact
/// 3. max_output_tokens_escalate — 8k → 64k 에스컬레이션
/// 4. max_output_tokens_recovery — 출력 잘림 시 재시도 (최대 3회)
/// 5. stop_hook_blocking — 훅이 실행 차단
/// 6. token_budget_continuation — 예산 내 계속 진행
/// 7. model_fallback — 모델 rate limit 시 대체 모델
pub struct ErrorRecoveryManager {
    pub state: RecoveryState,
}

impl ErrorRecoveryManager {
    pub fn new() -> Self {
        Self {
            state: RecoveryState::new(),
        }
    }

    /// 에러 유형을 분석하고 적절한 복구 전략을 반환
    pub fn attempt_recovery(
        &mut self,
        error_msg: &str,
        current_model: &str,
    ) -> RecoveryAction {
        let error_type = detect_error_type(error_msg);

        match error_type {
            ErrorType::Unknown => {
                self.state.consecutive_failures += 1;
                if self.state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES {
                    warn!(
                        failures = self.state.consecutive_failures,
                        "Consecutive failures exceeded, giving up"
                    );
                    RecoveryAction::GiveUp {
                        reason: format!("consecutive_failures: {}", error_msg),
                    }
                } else {
                    RecoveryAction::GiveUp {
                        reason: format!("unknown_error: {}", error_msg),
                    }
                }
            }

            // 1-2. prompt_too_long → reactive compact
            ErrorType::PromptTooLong => {
                if !self.state.has_attempted_reactive_compact {
                    self.state.has_attempted_reactive_compact = true;
                    info!("413 → reactive compact attempt");
                    RecoveryAction::Compact {
                        strategy: "summarize_history".to_string(),
                    }
                } else {
                    warn!("413 recurred after compact, giving up");
                    RecoveryAction::GiveUp {
                        reason: "prompt_too_long_after_compact".to_string(),
                    }
                }
            }

            // 3-4. max_output_tokens → 에스컬레이션
            ErrorType::MaxOutputTokens => {
                if self.state.max_output_tokens_override.is_none() {
                    self.state.max_output_tokens_override = Some(ESCALATED_MAX_TOKENS);
                    info!(
                        from = DEFAULT_MAX_TOKENS,
                        to = ESCALATED_MAX_TOKENS,
                        "Max output tokens escalation"
                    );
                    RecoveryAction::Escalate {
                        new_max_tokens: ESCALATED_MAX_TOKENS,
                    }
                } else if self.state.max_output_tokens_recovery_count < MAX_OUTPUT_RECOVERY_LIMIT {
                    self.state.max_output_tokens_recovery_count += 1;
                    info!(
                        attempt = self.state.max_output_tokens_recovery_count,
                        max = MAX_OUTPUT_RECOVERY_LIMIT,
                        "Max output tokens recovery"
                    );
                    RecoveryAction::Retry {
                        hint: Some(
                            "Output token limit hit. Resume directly — no apology, no summary of prior work."
                                .to_string(),
                        ),
                    }
                } else {
                    RecoveryAction::GiveUp {
                        reason: "max_output_tokens_exhausted".to_string(),
                    }
                }
            }

            // 7. rate_limit → model fallback
            ErrorType::RateLimit => {
                if !self.state.fallback_attempted {
                    if let Some(fb) = fallback_model(current_model) {
                        self.state.fallback_attempted = true;
                        self.state.original_model = Some(current_model.to_string());
                        info!(
                            from = current_model,
                            to = fb,
                            "Rate limit → model fallback"
                        );
                        RecoveryAction::Fallback {
                            original_model: current_model.to_string(),
                            fallback_model: fb.to_string(),
                        }
                    } else {
                        RecoveryAction::GiveUp {
                            reason: "rate_limit_no_fallback".to_string(),
                        }
                    }
                } else {
                    RecoveryAction::GiveUp {
                        reason: "rate_limit_fallback_already_attempted".to_string(),
                    }
                }
            }

            // timeout → 단순 재시도
            ErrorType::Timeout => {
                if self.state.consecutive_failures < 2 {
                    self.state.consecutive_failures += 1;
                    info!("Timeout → retry");
                    RecoveryAction::Retry { hint: None }
                } else {
                    RecoveryAction::GiveUp {
                        reason: "timeout_exhausted".to_string(),
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_error_type() {
        assert_eq!(
            detect_error_type("context_length_exceeded"),
            ErrorType::PromptTooLong
        );
        assert_eq!(
            detect_error_type("max_tokens reached"),
            ErrorType::MaxOutputTokens
        );
        assert_eq!(
            detect_error_type("rate limit exceeded"),
            ErrorType::RateLimit
        );
        assert_eq!(
            detect_error_type("connection timed out"),
            ErrorType::Timeout
        );
        assert_eq!(
            detect_error_type("some random error"),
            ErrorType::Unknown
        );
    }

    #[test]
    fn test_escalation() {
        let mut mgr = ErrorRecoveryManager::new();
        let action = mgr.attempt_recovery("max_tokens exceeded", "claude-sonnet-4-5-20250929");
        match action {
            RecoveryAction::Escalate { new_max_tokens } => {
                assert_eq!(new_max_tokens, ESCALATED_MAX_TOKENS);
            }
            _ => panic!("Expected Escalate"),
        }
    }

    #[test]
    fn test_model_fallback() {
        let mut mgr = ErrorRecoveryManager::new();
        let action = mgr.attempt_recovery("rate limit exceeded", "claude-opus-4-6");
        match action {
            RecoveryAction::Fallback {
                fallback_model, ..
            } => {
                assert_eq!(fallback_model, "claude-sonnet-4-5-20250929");
            }
            _ => panic!("Expected Fallback"),
        }
    }

    #[test]
    fn test_413_compact() {
        let mut mgr = ErrorRecoveryManager::new();
        let action = mgr.attempt_recovery("413 context_length_exceeded", "gpt-4o");
        match action {
            RecoveryAction::Compact { .. } => {}
            _ => panic!("Expected Compact"),
        }
        // 두 번째 413은 give_up
        let action2 = mgr.attempt_recovery("413 context_length_exceeded", "gpt-4o");
        match action2 {
            RecoveryAction::GiveUp { .. } => {}
            _ => panic!("Expected GiveUp after second 413"),
        }
    }
}
