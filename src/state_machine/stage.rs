use serde::{Deserialize, Serialize};

/// 하네스 파이프라인의 개별 단계.
/// 사용자가 체크리스트로 선택 — 포함된 단계만 실행됨.
///
/// 12단계 풀 파이프라인:
/// Bootstrap → MemoryRead → ContextBuild → Plan → ToolDiscovery
///   → ContextCompact → LLMCall ↔ ToolExecute (루프)
///   → Validate → Decide → MemoryWrite → Complete
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HarnessStage {
    // ── Phase 1: 초기화 ──────────────────────────────────────
    /// API 키 확인, 기본 설정 초기화
    Bootstrap,
    /// 이전 실행 컨텍스트 프리페치 (키워드 매칭)
    MemoryRead,
    /// PromptSectionManager로 시스템 프롬프트 + 입력 메시지 조립
    ContextBuild,

    // ── Phase 2: 계획 ──────────────────────────────────────
    /// 스프린트 계약 (목표, 필요정보, 검색전략, 완료기준)
    Plan,
    /// MCP 도구 탐색 + 시스템 프롬프트에 도구 인덱스 주입
    ToolDiscovery,

    // ── Phase 3: 실행 ──────────────────────────────────────
    /// 컨텍스트 버짓 체크 + 자동 압축 (History Snip / RAG 축소)
    ContextCompact,
    /// LLM API 호출 (스트리밍). tool_calls 있으면 ToolExecute로 점프.
    LLMCall,
    /// MCP 도구 실행 (Read=병렬, Write=직렬). 완료 후 LLMCall로 복귀.
    ToolExecute,

    // ── Phase 4: 검증 ──────────────────────────────────────
    /// 독립 평가 LLM 호출 (관련성/완전성/정확성, 0~1 채점)
    Validate,
    /// 재시도/통과 결정 (score < threshold → Plan으로 점프)
    Decide,

    // ── Phase 5: 마무리 ─────────────────────────────────────
    /// 실행 결과를 DB 로그(harness_execution_log)에 저장
    MemoryWrite,
    /// 최종 출력 반환, 메트릭 수집
    Complete,

    // ── Compat ──────────────────────────────────────────────
    /// 레거시: Bootstrap+MemoryRead+ContextBuild 통합 (하위 호환용)
    Init,
    /// 레거시: LLMCall+ToolExecute 통합 루프 (하위 호환용)
    Execute,
    /// 에러 종료
    Error,
}

impl HarnessStage {
    /// 프리셋별 기본 단계 구성
    ///
    /// | 프리셋 | 단계 수 | 용도 |
    /// |--------|---------|------|
    /// | minimal | 4 | 단순 대화 (도구 없음) |
    /// | claude_code | 7 | 계획 + 도구 사용 |
    /// | anthropic | 11 | 전체 루프 (검증/재시도 포함) |
    /// | full | 12 | 모든 단계 |
    /// 프리셋별 단계 구성.
    /// 사용자 표시 ID 문자열 배열 → from_str() 로 파싱하여 반환.
    pub fn preset(name: &str) -> Vec<HarnessStage> {
        let ids: &[&str] = match name {
            // 4단계: 단순 대화
            "minimal" | "none" => &["input", "system_prompt", "llm", "complete"],

            // 6단계: 도구 사용
            "standard" | "claude_code" => &[
                "input", "system_prompt", "tool_index", "llm", "execute", "complete",
            ],

            // 8단계: 도구 + 검증/재시도 + 저장
            "full" | "anthropic" => &[
                "input", "system_prompt", "tool_index", "llm", "execute",
                "validate", "decide", "save", "complete",
            ],

            // 하위 호환
            "basic" => return Self::preset("minimal"),
            _ => return Self::preset("minimal"),
        };

        ids.iter()
            .filter_map(|id| HarnessStage::from_str(id))
            .collect()
    }

    pub fn display_name(&self) -> &'static str {
        match self {
            HarnessStage::Bootstrap => "Input",
            HarnessStage::MemoryRead => "Memory",
            HarnessStage::ContextBuild => "System Prompt",
            HarnessStage::Plan => "Plan",
            HarnessStage::ToolDiscovery => "Tool Index",
            HarnessStage::ContextCompact => "Context",
            HarnessStage::LLMCall => "LLM",
            HarnessStage::ToolExecute => "Execute",
            HarnessStage::Validate => "Validate",
            HarnessStage::Decide => "Decide",
            HarnessStage::MemoryWrite => "Save",
            HarnessStage::Complete => "Complete",
            HarnessStage::Init => "Init",
            HarnessStage::Execute => "Execute(legacy)",
            HarnessStage::Error => "Error",
        }
    }

    pub fn display_name_ko(&self) -> &'static str {
        match self {
            HarnessStage::Bootstrap => "입력 설정",
            HarnessStage::MemoryRead => "이전 기억",
            HarnessStage::ContextBuild => "시스템 프롬프트",
            HarnessStage::Plan => "실행 계획",
            HarnessStage::ToolDiscovery => "도구 인덱싱",
            HarnessStage::ContextCompact => "컨텍스트 최적화",
            HarnessStage::LLMCall => "LLM 호출",
            HarnessStage::ToolExecute => "도구 실행",
            HarnessStage::Validate => "품질 검증",
            HarnessStage::Decide => "재시도 결정",
            HarnessStage::MemoryWrite => "결과 저장",
            HarnessStage::Complete => "완료",
            HarnessStage::Init => "초기화(레거시)",
            HarnessStage::Execute => "실행(레거시)",
            HarnessStage::Error => "에러",
        }
    }

    pub fn description_ko(&self) -> &'static str {
        match self {
            HarnessStage::Bootstrap => "API 키 확인, 설정 초기화",
            HarnessStage::MemoryRead => "이전 실행 결과에서 관련 내용 불러오기 (키워드 매칭)",
            HarnessStage::ContextBuild => "에이전트 역할/지시사항 조립 + 입력 메시지 구성",
            HarnessStage::Plan => "목표 선언, 검색 전략, 완료 기준 수립 (스프린트 계약)",
            HarnessStage::ToolDiscovery => "연결된 MCP 도구 목록 탐색 → 시스템 프롬프트에 주입",
            HarnessStage::ContextCompact => "토큰 한도 초과 시 자동 압축 (3단계: 잘라내기→RAG축소→요약)",
            HarnessStage::LLMCall => "LLM API 스트리밍 호출 → tool_calls 있으면 Execute로",
            HarnessStage::ToolExecute => "MCP / API / Document 도구 실행 → LLM으로 결과 반환",
            HarnessStage::Validate => "독립 평가 LLM: 관련성·완전성·정확성 채점 (0~1)",
            HarnessStage::Decide => "점수 threshold 미달 시 Plan으로 재시도 (최대 3회)",
            HarnessStage::MemoryWrite => "실행 결과·토큰·평가점수 DB 로그 저장",
            HarnessStage::Complete => "최종 출력 반환, 메트릭 수집, 세션 종료",
            HarnessStage::Init => "Bootstrap+MemoryRead+ContextBuild 통합 (레거시)",
            HarnessStage::Execute => "LLMCall+ToolExecute 통합 루프 (레거시)",
            HarnessStage::Error => "에러 종료",
        }
    }

    pub fn phase(&self) -> &'static str {
        match self {
            HarnessStage::Bootstrap | HarnessStage::MemoryRead | HarnessStage::ContextBuild | HarnessStage::Init => "init",
            HarnessStage::Plan | HarnessStage::ToolDiscovery => "plan",
            HarnessStage::ContextCompact | HarnessStage::LLMCall | HarnessStage::ToolExecute | HarnessStage::Execute => "exec",
            HarnessStage::Validate | HarnessStage::Decide => "validate",
            HarnessStage::MemoryWrite | HarnessStage::Complete => "finish",
            HarnessStage::Error => "error",
        }
    }

    /// 문자열에서 단계 파싱 — 사용자 표시명 및 기술 ID 모두 허용
    ///
    /// 사용자 표시명 (UI에서 사용):
    ///   input, memory, system_prompt, plan, tool_index,
    ///   context, llm, execute, validate, decide, save, complete
    pub fn from_str(s: &str) -> Option<HarnessStage> {
        match s.to_lowercase().as_str() {
            // ── 사용자 표시명 (UI / API / preset에서 사용) ──
            "input"         => Some(HarnessStage::Bootstrap),
            "memory"        => Some(HarnessStage::MemoryRead),
            "system_prompt" | "systemprompt" => Some(HarnessStage::ContextBuild),
            "plan"          => Some(HarnessStage::Plan),
            "tool_index"    | "toolindex"    => Some(HarnessStage::ToolDiscovery),
            "context"       => Some(HarnessStage::ContextCompact),
            "llm"           => Some(HarnessStage::LLMCall),
            "execute"       => Some(HarnessStage::ToolExecute),
            "validate"      => Some(HarnessStage::Validate),
            "decide"        => Some(HarnessStage::Decide),
            "save"          => Some(HarnessStage::MemoryWrite),
            "complete"      => Some(HarnessStage::Complete),

            // ── 내부 기술 ID (하위 호환) ────────────────────
            "bootstrap"                       => Some(HarnessStage::Bootstrap),
            "memory_read"  | "memoryread"     => Some(HarnessStage::MemoryRead),
            "context_build"| "contextbuild"   => Some(HarnessStage::ContextBuild),
            "tool_discovery"|"tooldiscovery"  => Some(HarnessStage::ToolDiscovery),
            "context_compact"|"contextcompact"=> Some(HarnessStage::ContextCompact),
            "llm_call"     | "llmcall"        => Some(HarnessStage::LLMCall),
            "tool_execute" | "toolexecute"    => Some(HarnessStage::ToolExecute),
            "memory_write" | "memorywrite"    => Some(HarnessStage::MemoryWrite),

            // ── 레거시 compat ───────────────────────────────
            "init"  => Some(HarnessStage::Init),
            "error" => Some(HarnessStage::Error),
            _ => None,
        }
    }

    /// 사용자 표시용 짧은 ID (UI / SSE 이벤트에서 stage_id로 전달)
    pub fn user_id(&self) -> &'static str {
        match self {
            HarnessStage::Bootstrap     => "input",
            HarnessStage::MemoryRead    => "memory",
            HarnessStage::ContextBuild  => "system_prompt",
            HarnessStage::Plan          => "plan",
            HarnessStage::ToolDiscovery => "tool_index",
            HarnessStage::ContextCompact=> "context",
            HarnessStage::LLMCall       => "llm",
            HarnessStage::ToolExecute   => "execute",
            HarnessStage::Validate      => "validate",
            HarnessStage::Decide        => "decide",
            HarnessStage::MemoryWrite   => "save",
            HarnessStage::Complete      => "complete",
            HarnessStage::Init          => "init",
            HarnessStage::Execute       => "execute_legacy",
            HarnessStage::Error         => "error",
        }
    }
}

/// 단계 실행 결과
#[derive(Debug, Clone, Serialize)]
pub struct StageResult {
    pub stage: HarnessStage,
    pub output: serde_json::Value,
    /// Validate 단계에서의 점수 (0.0 ~ 1.0)
    pub score: Option<f64>,
    /// 에러 발생 시
    pub error: Option<String>,
}

/// 상태 전이 결정
#[derive(Debug, Clone)]
pub enum StageTransition {
    /// 다음 단계로 진행
    Next,
    /// 특정 단계로 점프 (재시도 또는 LLMCall↔ToolExecute 루프)
    JumpTo(HarnessStage),
    /// 완료
    Complete(serde_json::Value),
    /// 에러로 종료
    Error(String),
}
