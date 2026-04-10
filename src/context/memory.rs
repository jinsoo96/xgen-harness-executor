//! Memory Prefetch — Python memory_prefetch.py (175줄) 포팅
//!
//! OpenClaude findRelevantMemories.ts 포팅.
//! 에이전트 실행 전에 관련 이전 컨텍스트를 선별하여 프리페치.

use std::collections::HashSet;
use tracing::info;

/// 원본 상수
const MAX_SURFACED_MEMORIES: usize = 5;
const MAX_RECENT_ACTIVITIES: usize = 5;

/// 메모리 프리페처 — 이전 실행 결과 선별 주입
///
/// 원본 동작:
/// 1. 이전 워크플로우 실행에서 관련 답변 검색
/// 2. 키워드 매칭으로 관련성 점수 계산
/// 3. 상위 5개 선택
/// 4. 시스템 프롬프트에 "이전 관련 컨텍스트"로 주입
pub struct MemoryPrefetcher {
    already_surfaced: HashSet<String>,
}

impl MemoryPrefetcher {
    pub fn new() -> Self {
        Self {
            already_surfaced: HashSet::new(),
        }
    }

    /// 이전 실행 결과에서 현재 질문과 관련된 컨텍스트를 선별
    ///
    /// `previous_results`: 이전 실행 결과 문자열 목록
    /// `current_query`: 현재 사용자 질문
    ///
    /// Returns: 시스템 프롬프트에 주입할 관련 컨텍스트 문자열
    pub fn prefetch(
        &mut self,
        current_query: &str,
        previous_results: &[String],
        user_feedback: &[String],
    ) -> String {
        let mut contexts = Vec::new();

        // 1. 이전 실행에서 관련 답변 검색
        if !previous_results.is_empty() {
            let relevant = self.select_relevant(current_query, previous_results);
            if !relevant.is_empty() {
                let section = format!(
                    "## 이전 관련 답변\n{}",
                    relevant
                        .iter()
                        .map(|r| format!("- {}", r))
                        .collect::<Vec<_>>()
                        .join("\n")
                );
                contexts.push(section);
            }
        }

        // 2. 사용자 피드백/선호
        if !user_feedback.is_empty() {
            let feedback_items: Vec<String> = user_feedback
                .iter()
                .take(3)
                .map(|f| format!("- {}", truncate(f, 100)))
                .collect();
            if !feedback_items.is_empty() {
                contexts.push(format!(
                    "## 사용자 선호\n{}",
                    feedback_items.join("\n")
                ));
            }
        }

        if !contexts.is_empty() {
            let result = contexts.join("\n\n");
            info!(
                sections = contexts.len(),
                chars = result.len(),
                "Memory prefetched"
            );
            result
        } else {
            String::new()
        }
    }

    /// 현재 질문과 관련된 후보를 키워드 매칭으로 선택
    /// (원본은 Sonnet LLM으로 선택하지만, 비용 절감을 위해 키워드 매칭)
    fn select_relevant(
        &mut self,
        query: &str,
        candidates: &[String],
    ) -> Vec<String> {
        if candidates.is_empty() {
            return Vec::new();
        }

        let query_words: HashSet<String> = query
            .to_lowercase()
            .split_whitespace()
            .map(String::from)
            .collect();

        let mut scored: Vec<(usize, &String)> = candidates
            .iter()
            .filter_map(|candidate| {
                let key = truncate(candidate, 50);
                if self.already_surfaced.contains(&key) {
                    return None;
                }

                let candidate_words: HashSet<String> = candidate
                    .to_lowercase()
                    .split_whitespace()
                    .map(String::from)
                    .collect();

                let overlap = query_words.intersection(&candidate_words).count();
                if overlap > 0 {
                    Some((overlap, candidate))
                } else {
                    None
                }
            })
            .collect();

        scored.sort_by(|a, b| b.0.cmp(&a.0));

        scored
            .into_iter()
            .take(MAX_SURFACED_MEMORIES)
            .map(|(_, candidate)| {
                let key = truncate(candidate, 50);
                self.already_surfaced.insert(key);
                truncate(candidate, 200)
            })
            .collect()
    }
}

fn truncate(s: &str, max_chars: usize) -> String {
    if s.len() <= max_chars {
        s.to_string()
    } else {
        format!("{}...", &s[..max_chars])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_keyword_matching() {
        let mut pf = MemoryPrefetcher::new();
        let candidates = vec![
            "React 컴포넌트 설계 패턴에 대한 설명".to_string(),
            "Python 데이터 처리 파이프라인".to_string(),
            "React hooks 사용법 가이드".to_string(),
        ];
        let result = pf.select_relevant("React 컴포넌트 만들기", &candidates);
        assert!(!result.is_empty());
        // React 관련 결과가 먼저 와야 함
        assert!(result[0].contains("React"));
    }

    #[test]
    fn test_dedup() {
        let mut pf = MemoryPrefetcher::new();
        let candidates = vec!["same result about React".to_string()];
        let r1 = pf.select_relevant("React", &candidates);
        let r2 = pf.select_relevant("React", &candidates);
        assert_eq!(r1.len(), 1);
        assert_eq!(r2.len(), 0); // 이미 표시됨
    }
}
