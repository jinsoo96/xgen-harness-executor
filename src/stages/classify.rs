//! 입력 복잡도 분류기 — 단순 인사/질문 vs 복잡한 분석 요청 판별
//!
//! "hi" → minimal (4단계)
//! "이 CSV 분석해줘" → standard (6단계)
//! "RAG 검색 후 보고서 작성해줘, 품질 검증까지" → full (8단계)
//!
//! LLM 호출 없이 규칙 기반으로 판별 (0ms).

/// 입력 복잡도 등급
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputComplexity {
    /// 단순 인사/짧은 질문 — Validate 불필요
    Simple,
    /// 도구 사용이 필요한 중간 복잡도
    Moderate,
    /// 복잡한 분석/검증이 필요한 요청
    Complex,
}

/// 단순 인사/대화 패턴
const SIMPLE_PATTERNS: &[&str] = &[
    "hi", "hello", "hey", "안녕", "ㅎㅇ", "하이",
    "감사", "고마워", "thanks", "thank you",
    "네", "응", "ㅇㅇ", "ok", "okay", "ㅇㅋ",
    "아니", "ㄴㄴ", "no", "nope",
    "뭐해", "뭐야", "누구야", "who are you",
    "bye", "잘가", "ㅂㅂ",
];

/// 도구 사용을 암시하는 키워드
const TOOL_KEYWORDS: &[&str] = &[
    "검색", "찾아", "search", "find", "look up",
    "분석", "analyze", "analysis",
    "파일", "file", "csv", "json", "pdf", "문서", "document",
    "API", "호출", "call", "request",
    "데이터", "data", "테이블", "table",
    "코드", "code", "함수", "function",
    "이미지", "image", "사진", "photo",
    "번역", "translate",
    "요약", "summarize", "summary",
    "작성", "write", "생성", "create", "만들어",
];

/// 복잡한 요청을 암시하는 키워드
const COMPLEX_KEYWORDS: &[&str] = &[
    "보고서", "report", "리포트",
    "비교", "compare", "comparison",
    "평가", "evaluate", "assessment",
    "검증", "verify", "validate", "확인해",
    "전략", "strategy", "계획", "plan",
    "여러", "multiple", "각각", "순서대로", "단계별",
    "종합", "comprehensive", "상세", "detailed",
    "RAG", "컬렉션", "collection",
];

/// 입력 텍스트의 복잡도를 판별 (LLM 호출 없이 규칙 기반)
pub fn classify_input(text: &str) -> InputComplexity {
    let trimmed = text.trim();
    let lower = trimmed.to_lowercase();

    // 1. 빈 입력 또는 매우 짧은 입력
    if trimmed.is_empty() || trimmed.len() <= 5 {
        return InputComplexity::Simple;
    }

    // 2. 단순 인사/대화 패턴 매칭
    // 정확 매칭 (공백/구두점 제거 후)
    let cleaned: String = lower.chars().filter(|c| c.is_alphanumeric() || *c > '\u{AC00}').collect();
    for pattern in SIMPLE_PATTERNS {
        let pattern_cleaned: String = pattern.chars().filter(|c| c.is_alphanumeric() || *c > '\u{AC00}').collect();
        if cleaned == pattern_cleaned {
            return InputComplexity::Simple;
        }
    }

    // 3. 단어 수 기반 1차 판별
    let word_count = trimmed.split_whitespace().count();
    let char_count = trimmed.chars().count();

    // 10자 이하 + 도구 키워드 없으면 Simple
    if char_count <= 15 && !has_keyword(&lower, TOOL_KEYWORDS) {
        return InputComplexity::Simple;
    }

    // 4. 복잡 키워드 체크
    let complex_score = count_keywords(&lower, COMPLEX_KEYWORDS);
    if complex_score >= 2 || (complex_score >= 1 && word_count > 20) {
        return InputComplexity::Complex;
    }

    // 5. 도구 키워드 체크
    let tool_score = count_keywords(&lower, TOOL_KEYWORDS);
    if tool_score >= 1 {
        return InputComplexity::Moderate;
    }

    // 6. 긴 텍스트 (50자 이상)이면 최소 Moderate
    if char_count > 50 {
        return InputComplexity::Moderate;
    }

    // 7. 짧은 질문
    InputComplexity::Simple
}

/// 복잡도에 맞는 프리셋 이름 반환
pub fn complexity_to_preset(complexity: InputComplexity) -> &'static str {
    match complexity {
        InputComplexity::Simple => "minimal",
        InputComplexity::Moderate => "standard",
        InputComplexity::Complex => "full",
    }
}

/// 현재 프리셋이 입력 복잡도에 비해 과도한지 판단
/// 과도하면 다운그레이드할 프리셋을 반환
pub fn should_downgrade(
    current_preset: &str,
    text: &str,
) -> Option<&'static str> {
    let complexity = classify_input(text);
    let recommended = complexity_to_preset(complexity);

    let preset_level = match current_preset {
        "minimal" | "none" => 0,
        "standard" | "claude_code" => 1,
        "anthropic" => 2,
        "full" => 3,
        _ => 0,
    };

    let recommended_level = match recommended {
        "minimal" => 0,
        "standard" => 1,
        "full" => 3,
        _ => 0,
    };

    if preset_level > recommended_level {
        Some(recommended)
    } else {
        None
    }
}

fn has_keyword(text: &str, keywords: &[&str]) -> bool {
    keywords.iter().any(|kw| text.contains(kw))
}

fn count_keywords(text: &str, keywords: &[&str]) -> usize {
    keywords.iter().filter(|kw| text.contains(**kw)).count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_greetings() {
        assert_eq!(classify_input("hi"), InputComplexity::Simple);
        assert_eq!(classify_input("안녕"), InputComplexity::Simple);
        assert_eq!(classify_input("Hello"), InputComplexity::Simple);
        assert_eq!(classify_input("ㅎㅇ"), InputComplexity::Simple);
        assert_eq!(classify_input("네"), InputComplexity::Simple);
        assert_eq!(classify_input("ok"), InputComplexity::Simple);
        assert_eq!(classify_input(""), InputComplexity::Simple);
        assert_eq!(classify_input("뭐해?"), InputComplexity::Simple);
    }

    #[test]
    fn test_moderate_requests() {
        assert_eq!(classify_input("이 CSV 파일 분석해줘"), InputComplexity::Moderate);
        assert_eq!(classify_input("최근 뉴스 검색해줘"), InputComplexity::Moderate);
        assert_eq!(classify_input("이 코드 설명해줘"), InputComplexity::Moderate);
        assert_eq!(classify_input("한국어로 번역해줘"), InputComplexity::Moderate);
    }

    #[test]
    fn test_complex_requests() {
        assert_eq!(
            classify_input("RAG 검색으로 관련 문서 찾고 비교 보고서 작성해줘"),
            InputComplexity::Complex
        );
        assert_eq!(
            classify_input("각 컬렉션에서 데이터를 검색한 후 종합 분석 보고서를 단계별로 만들어줘"),
            InputComplexity::Complex
        );
    }

    #[test]
    fn test_downgrade() {
        assert_eq!(should_downgrade("full", "hi"), Some("minimal"));
        assert_eq!(should_downgrade("full", "이 파일 분석해줘"), Some("standard"));
        assert_eq!(should_downgrade("minimal", "hi"), None); // 이미 minimal
        assert_eq!(should_downgrade("full", "RAG 검색 후 비교 보고서 작성"), None); // 복잡 → full 유지
    }
}
