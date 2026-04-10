//! Context Window Manager — Python context_manager.py (215줄) 포팅
//!
//! OpenClaude autoCompact.ts 포팅.
//! 에이전트에게 "무한 컨텍스트" 환경을 제공 — 실제로는 예산을 관리하며
//! 필요할 때 자동으로 이전 대화를 요약하고, 중요한 정보만 유지.

use std::collections::HashMap;
use tracing::{debug, info, warn};

/// OpenClaude 원본 상수
const AUTOCOMPACT_BUFFER_TOKENS: u64 = 13_000;
const MAX_CONSECUTIVE_FAILURES: u32 = 3;
/// prompts.py 추가 상수
const WARNING_THRESHOLD_BUFFER_TOKENS: u64 = 20_000;
const ERROR_THRESHOLD_BUFFER_TOKENS: u64 = 20_000;

/// 1자 ≈ 0.5토큰 (한국어 기준 보수적)
const CHARS_PER_TOKEN: f64 = 2.0;

/// Provider별 컨텍스트 윈도우 (tokens)
fn context_window_for_provider(provider: &str) -> u64 {
    match provider {
        "openai" => 128_000,
        "anthropic" => 200_000,
        "google" => 1_000_000,
        "bedrock" => 200_000,
        "vllm" => 32_000,
        _ => 128_000,
    }
}

/// OpenClaude AutoCompactTrackingState 포팅
#[derive(Debug, Clone, Default)]
pub struct CompactTrackingState {
    pub compacted: bool,
    pub turn_counter: u32,
    pub consecutive_failures: u32,
}

/// 컨텍스트 예산 추적
#[derive(Debug, Clone)]
pub struct ContextBudget {
    pub provider: String,
    pub max_tokens: u64,
    pub autocompact_threshold: u64,
    pub current_usage_chars: u64,
    pub tracking: CompactTrackingState,
}

impl ContextBudget {
    pub fn new(provider: &str) -> Self {
        let max_tokens = context_window_for_provider(provider);
        Self {
            provider: provider.to_string(),
            max_tokens,
            autocompact_threshold: max_tokens.saturating_sub(AUTOCOMPACT_BUFFER_TOKENS),
            current_usage_chars: 0,
            tracking: CompactTrackingState::default(),
        }
    }

    pub fn current_usage_tokens(&self) -> u64 {
        (self.current_usage_chars as f64 / CHARS_PER_TOKEN) as u64
    }

    pub fn percent_used(&self) -> f64 {
        if self.autocompact_threshold == 0 {
            return 100.0;
        }
        let pct = (self.current_usage_tokens() as f64 / self.autocompact_threshold as f64) * 100.0;
        pct.min(100.0)
    }

    pub fn needs_compaction(&self) -> bool {
        self.current_usage_tokens() >= self.autocompact_threshold
    }

    /// 연속 실패 회로차단기 (OpenClaude: MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3)
    pub fn is_circuit_broken(&self) -> bool {
        self.tracking.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
    }

    /// 토큰 경고 상태 계산 (Python prompts.py calculate_token_warning_state 포팅)
    pub fn token_warning_state(&self) -> TokenWarningState {
        let usage = self.current_usage_tokens();
        let warning_threshold = self.max_tokens.saturating_sub(WARNING_THRESHOLD_BUFFER_TOKENS);
        let error_threshold = self.max_tokens.saturating_sub(ERROR_THRESHOLD_BUFFER_TOKENS);
        let remaining = self.max_tokens.saturating_sub(usage);
        let percent_left = if self.max_tokens > 0 {
            ((remaining as f64 / self.max_tokens as f64) * 100.0) as u32
        } else {
            0
        };

        TokenWarningState {
            percent_left,
            is_above_warning: usage >= warning_threshold,
            is_above_error: usage >= error_threshold,
            is_above_autocompact: usage >= self.autocompact_threshold,
            is_at_blocking_limit: usage >= self.max_tokens,
        }
    }
}

/// 토큰 경고 상태
#[derive(Debug, Clone)]
pub struct TokenWarningState {
    pub percent_left: u32,
    pub is_above_warning: bool,
    pub is_above_error: bool,
    pub is_above_autocompact: bool,
    pub is_at_blocking_limit: bool,
}

/// 에이전트 컨텍스트 자동 관리자
///
/// 3단계 압축:
/// 1. History Snip — 오래된 대화 잘라내기 (최근 4개 유지)
/// 2. Microcompact — RAG 결과를 인덱스만 남기기
/// 3. Autocompact — LLM 기반 대화 요약
pub struct ContextWindowManager {
    pub budget: ContextBudget,
    zone_usage: HashMap<String, u64>,
}

impl ContextWindowManager {
    pub fn new(provider: &str) -> Self {
        Self {
            budget: ContextBudget::new(provider),
            zone_usage: HashMap::new(),
        }
    }

    /// 특정 존의 콘텐츠 크기를 추적
    pub fn track(&mut self, zone: &str, content: &str) {
        let chars = content.len() as u64;
        let old = self.zone_usage.get(zone).copied().unwrap_or(0);
        self.zone_usage.insert(zone.to_string(), chars);
        // 차이만큼 전체 사용량 업데이트
        if chars > old {
            self.budget.current_usage_chars += chars - old;
        } else {
            self.budget.current_usage_chars = self.budget.current_usage_chars.saturating_sub(old - chars);
        }
    }

    /// 현재 컨텍스트 상태 반환
    pub fn get_status(&self) -> serde_json::Value {
        let zones: HashMap<String, u64> = self
            .zone_usage
            .iter()
            .map(|(k, v)| (k.clone(), (*v as f64 / CHARS_PER_TOKEN) as u64))
            .collect();

        serde_json::json!({
            "provider": self.budget.provider,
            "max_tokens": self.budget.max_tokens,
            "current_tokens": self.budget.current_usage_tokens(),
            "percent_used": (self.budget.percent_used() * 10.0).round() / 10.0,
            "needs_compaction": self.budget.needs_compaction(),
            "circuit_broken": self.budget.is_circuit_broken(),
            "zones": zones,
        })
    }

    /// 컨텍스트 예산 체크 + 필요 시 자동 압축
    ///
    /// Returns: (system_prompt, chat_history, rag_context) — 압축 적용된 버전
    pub fn check_and_compact(
        &mut self,
        system_prompt: &str,
        chat_history: &mut Vec<serde_json::Value>,
        rag_context: &mut String,
    ) -> bool {
        // 현재 사용량 갱신
        self.track("system_prompt", system_prompt);
        self.track("rag_context", rag_context);
        self.track("chat_history", &serde_json::to_string(chat_history).unwrap_or_default());

        if !self.budget.needs_compaction() {
            debug!(
                percent = self.budget.percent_used(),
                "Context budget OK"
            );
            return false;
        }

        if self.budget.is_circuit_broken() {
            warn!(
                failures = self.budget.tracking.consecutive_failures,
                "Circuit breaker active, skipping compaction"
            );
            return false;
        }

        info!(
            percent = self.budget.percent_used(),
            threshold = self.budget.autocompact_threshold,
            "Compaction needed"
        );

        let mut compacted = false;

        // 1단계: History Snip — 오래된 대화 잘라내기
        if chat_history.len() > 4 {
            let original_len = chat_history.len();
            let keep = chat_history.split_off(chat_history.len() - 4);
            *chat_history = keep;
            info!(
                original = original_len,
                remaining = chat_history.len(),
                "History snip applied"
            );
            self.track("chat_history", &serde_json::to_string(chat_history).unwrap_or_default());
            compacted = true;
        }

        // 2단계: RAG 결과 축소
        if rag_context.len() > 5000 {
            let original_len = rag_context.len();
            // 인덱스 부분만 유지
            let lines: Vec<&str> = rag_context.lines().collect();
            let index_lines: Vec<&str> = lines
                .iter()
                .filter(|l| {
                    l.starts_with("- [DOC_") || l.starts_with("##") || l.starts_with("상세")
                })
                .copied()
                .take(20)
                .collect();

            if !index_lines.is_empty() {
                *rag_context = index_lines.join("\n");
            } else {
                let truncated = if rag_context.len() > 3000 {
                    format!(
                        "{}\n\n[컨텍스트 압축: 상세 내용 생략]",
                        &rag_context[..3000]
                    )
                } else {
                    rag_context.clone()
                };
                *rag_context = truncated;
            }

            info!(
                original = original_len,
                remaining = rag_context.len(),
                "RAG context compacted"
            );
            self.track("rag_context", rag_context);
            compacted = true;
        }

        // 3단계: LLM 기반 요약은 Execute 단계에서 provider를 통해 수행
        // (ContextWindowManager는 LLM 의존성 없이 순수 데이터 관리)
        // 호출자가 needs_llm_summary()를 체크하여 별도 처리

        self.budget.tracking.turn_counter += 1;
        compacted
    }

    /// LLM 요약이 필요한지 확인 (3단계 압축 후에도 예산 초과 시)
    pub fn needs_llm_summary(&self) -> bool {
        self.budget.needs_compaction() && !self.budget.is_circuit_broken()
    }

    /// LLM 요약 성공 시 호출
    pub fn on_summary_success(&mut self) {
        self.budget.tracking.compacted = true;
        self.budget.tracking.consecutive_failures = 0;
    }

    /// LLM 요약 실패 시 호출
    pub fn on_summary_failure(&mut self) {
        self.budget.tracking.consecutive_failures += 1;
        warn!(
            failures = self.budget.tracking.consecutive_failures,
            max = MAX_CONSECUTIVE_FAILURES,
            "LLM summary failed"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_budget_creation() {
        let budget = ContextBudget::new("anthropic");
        assert_eq!(budget.max_tokens, 200_000);
        assert_eq!(budget.autocompact_threshold, 200_000 - 13_000);
        assert!(!budget.needs_compaction());
        assert!(!budget.is_circuit_broken());
    }

    #[test]
    fn test_compaction_trigger() {
        let mut mgr = ContextWindowManager::new("vllm"); // 32K window
        // vllm: threshold = 32000 - 13000 = 19000 tokens = 38000 chars
        mgr.track("test", &"a".repeat(40_000));
        assert!(mgr.budget.needs_compaction());
    }

    #[test]
    fn test_circuit_breaker() {
        let mut budget = ContextBudget::new("openai");
        assert!(!budget.is_circuit_broken());
        budget.tracking.consecutive_failures = 3;
        assert!(budget.is_circuit_broken());
    }

    #[test]
    fn test_history_snip() {
        let mut mgr = ContextWindowManager::new("vllm");
        let mut history: Vec<serde_json::Value> = (0..10)
            .map(|i| serde_json::json!({"role": "user", "content": format!("msg {}", i)}))
            .collect();
        let mut rag = String::new();
        // Force compaction by filling budget
        mgr.budget.current_usage_chars = 40_000;
        mgr.check_and_compact("system", &mut history, &mut rag);
        assert_eq!(history.len(), 4); // 최근 4개만 유지
    }
}
