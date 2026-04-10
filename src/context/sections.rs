//! Prompt Section Manager — Python prompt_sections.py (199줄) 포팅
//!
//! OpenClaude systemPromptSections.ts 포팅.
//! 프롬프트를 한 덩어리가 아닌 섹션별로 분리.
//! 각 섹션은 캐시 가능/불가, 우선순위, 조건부 포함/제외.

use std::collections::HashMap;
use tracing::info;

/// 프롬프트 섹션 하나
pub struct PromptSection {
    pub name: String,
    pub content: Option<String>,
    /// True면 세션 동안 재사용 가능
    pub cacheable: bool,
    /// 컨텍스트 예산 부족 시 제거 순서 (숫자 클수록 먼저 제거)
    pub priority: u32,
    /// 포함 조건
    pub active: bool,
}

impl PromptSection {
    pub fn is_active(&self) -> bool {
        if let Some(ref content) = self.content {
            self.active && !content.trim().is_empty()
        } else {
            false
        }
    }

    /// 토큰 수 추정 (1자 ≈ 0.5토큰)
    pub fn token_estimate(&self) -> usize {
        self.content.as_ref().map(|c| c.len() / 2).unwrap_or(0)
    }
}

/// 시스템 프롬프트를 섹션 단위로 관리
pub struct PromptSectionManager {
    sections: Vec<PromptSection>,
    cache: HashMap<String, String>,
}

impl PromptSectionManager {
    pub fn new() -> Self {
        Self {
            sections: Vec::new(),
            cache: HashMap::new(),
        }
    }

    /// 섹션 추가
    pub fn add(
        &mut self,
        name: &str,
        content: Option<String>,
        cacheable: bool,
        priority: u32,
        active: bool,
    ) -> &mut Self {
        self.sections.push(PromptSection {
            name: name.to_string(),
            content,
            cacheable,
            priority,
            active,
        });
        self
    }

    /// 역할 정의 섹션 (priority 1, 절대 제거 안 됨)
    pub fn add_role(&mut self, content: &str) -> &mut Self {
        self.add("role", Some(content.to_string()), true, 1, true)
    }

    /// 스프린트 계약 섹션 (priority 2)
    pub fn add_sprint_contract(&mut self, content: &str, enabled: bool) -> &mut Self {
        self.add("sprint_contract", Some(content.to_string()), true, 2, enabled)
    }

    /// 계획 컨텍스트 (priority 3)
    pub fn add_plan(&mut self, content: &str) -> &mut Self {
        if content.is_empty() {
            return self;
        }
        self.add("plan", Some(content.to_string()), true, 3, true)
    }

    /// 도구 사용 지침 (priority 4)
    pub fn add_tool_guidelines(&mut self, tool_names: &[String]) -> &mut Self {
        let tools_list = if tool_names.is_empty() {
            "(없음)".to_string()
        } else {
            tool_names.join(", ")
        };
        let content = format!(
            "## 도구 사용 지침\n\n\
             사용 가능한 도구: {tools_list}\n\n\
             ### 도구 선택 원칙\n\
             - 정보가 필요하면 먼저 검색/조회 도구를 사용하세요\n\
             - 한 번에 여러 도구를 호출할 수 있으면 병렬로 요청하세요\n\
             - 도구 결과가 불충분하면 다른 도구나 다른 쿼리로 재시도하세요\n\
             - 도구 없이 답할 수 있으면 도구를 사용하지 마세요\n\n\
             ### 결과 처리\n\
             - 도구 결과를 그대로 복사하지 말고, 핵심만 추출하여 답변에 활용하세요\n\
             - 여러 도구 결과를 종합하여 일관된 답변을 구성하세요"
        );
        self.add("tool_guidelines", Some(content), true, 4, true)
    }

    /// 톤 & 스타일 (priority 8, 예산 부족 시 제거 가능)
    pub fn add_tone_style(&mut self, style: &str) -> &mut Self {
        let style_line = if style.is_empty() {
            String::new()
        } else {
            format!("\n사용자 지정 스타일: {style}")
        };
        let content = format!(
            "## 톤 & 스타일\n\n\
             - 한국어로 답변하세요 (기술 용어는 영어 유지)\n\
             - 핵심부터 말하고, 이유는 그 다음에 설명하세요\n\
             - 불확실한 정보는 명확히 표시하세요\n\
             - 코드 예시가 도움되면 포함하세요{style_line}"
        );
        self.add("tone_style", Some(content), true, 8, true)
    }

    /// 출력 효율 (priority 9, 가장 먼저 제거)
    pub fn add_output_efficiency(&mut self) -> &mut Self {
        let content = "\
            ## 출력 효율\n\n\
            - 답변은 요청에 비례하는 길이로 작성하세요\n\
            - 한 문장으로 답할 수 있으면 한 문장으로 답하세요\n\
            - 불필요한 서론, 반복, 요약 금지\n\
            - \"네, 알겠습니다\" 같은 필러 없이 바로 본론으로"
            .to_string();
        self.add("output_efficiency", Some(content), true, 9, true)
    }

    /// 환경 정보 (priority 6, 동적 — 매 실행마다 갱신)
    pub fn add_environment_info(
        &mut self,
        model: &str,
        workflow_id: &str,
        provider: &str,
        date: &str,
    ) -> &mut Self {
        let content = format!(
            "## 실행 환경\n\n\
             - 모델: `{model}`\n\
             - 프로바이더: {provider}\n\
             - 워크플로우: `{workflow_id}`\n\
             - 날짜: {date}"
        );
        self.add("environment_info", Some(content), false, 6, true)
    }

    /// RAG 인덱스 (priority 5, 동적)
    pub fn add_rag_index(&mut self, content: &str) -> &mut Self {
        if content.is_empty() {
            return self;
        }
        self.add("rag_index", Some(content.to_string()), false, 5, true)
    }

    /// 대화 컨텍스트 (priority 7, 가장 먼저 제거)
    pub fn add_chat_context(&mut self, content: &str) -> &mut Self {
        if content.is_empty() {
            return self;
        }
        self.add("chat_context", Some(content.to_string()), false, 7, true)
    }

    /// 활성 섹션을 조합하여 최종 프롬프트 생성
    ///
    /// max_tokens가 지정되면 우선순위 낮은(숫자 큰) 섹션부터 제거
    pub fn build(&mut self, max_tokens: Option<usize>) -> String {
        let mut active: Vec<&PromptSection> = self
            .sections
            .iter()
            .filter(|s| s.is_active())
            .collect();

        if active.is_empty() {
            return String::new();
        }

        // 예산 체크
        if let Some(max) = max_tokens {
            let total: usize = active.iter().map(|s| s.token_estimate()).sum();
            if total > max {
                // priority 기준 정렬 (낮은 숫자 먼저 = 중요한 것 먼저)
                active.sort_by_key(|s| s.priority);
                // 뒤에서부터 (높은 priority = 덜 중요) 제거
                while active.iter().map(|s| s.token_estimate()).sum::<usize>() > max
                    && active.len() > 1
                {
                    let removed = active.pop().unwrap();
                    info!(
                        section = %removed.name,
                        tokens = removed.token_estimate(),
                        "Removed section due to budget"
                    );
                }
            }
        }

        // 캐시 활용 + 조합
        let mut parts: Vec<String> = Vec::new();
        for section in &active {
            let content = if section.cacheable {
                if let Some(cached) = self.cache.get(&section.name) {
                    cached.clone()
                } else {
                    let c = section.content.as_deref().unwrap_or("").to_string();
                    self.cache.insert(section.name.clone(), c.clone());
                    c
                }
            } else {
                section.content.as_deref().unwrap_or("").to_string()
            };
            parts.push(content);
        }

        let total_tokens: usize = active.iter().map(|s| s.token_estimate()).sum();
        info!(
            sections = active.len(),
            tokens = total_tokens,
            "Prompt sections assembled"
        );

        parts.join("\n\n")
    }

    /// 섹션 상태 리포트
    pub fn get_report(&self) -> Vec<serde_json::Value> {
        self.sections
            .iter()
            .map(|s| {
                serde_json::json!({
                    "name": s.name,
                    "active": s.is_active(),
                    "cacheable": s.cacheable,
                    "priority": s.priority,
                    "tokens": s.token_estimate(),
                })
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_build() {
        let mut mgr = PromptSectionManager::new();
        mgr.add_role("You are a helpful assistant.");
        mgr.add_sprint_contract("Plan your work.", true);

        let result = mgr.build(None);
        assert!(result.contains("helpful assistant"));
        assert!(result.contains("Plan your work"));
    }

    #[test]
    fn test_budget_removal() {
        let mut mgr = PromptSectionManager::new();
        mgr.add_role("Short role."); // priority 1, ~6 tokens
        mgr.add("big_section", Some("x".repeat(10000)), false, 7, true); // ~5000 tokens

        let result = mgr.build(Some(100));
        // big_section이 제거되어야 함
        assert!(result.contains("Short role"));
        assert!(!result.contains(&"x".repeat(100)));
    }

    #[test]
    fn test_inactive_excluded() {
        let mut mgr = PromptSectionManager::new();
        mgr.add_role("Role.");
        mgr.add_sprint_contract("Sprint.", false); // inactive

        let result = mgr.build(None);
        assert!(result.contains("Role"));
        assert!(!result.contains("Sprint"));
    }
}
