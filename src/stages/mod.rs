// ── Phase 1: 초기화 ─────────────────────────
pub mod bootstrap;        // 1. API 키 확인, 설정 초기화
pub mod memory_read;      // 2. 이전 실행 컨텍스트 프리페치
pub mod context_build;    // 3. 시스템 프롬프트 + 입력 메시지 조립

// ── Phase 2: 계획 ───────────────────────────
pub mod plan;             // 4. 스프린트 계약 (목표/전략/완료기준)
pub mod tool_discovery;   // 5. MCP 도구 탐색 + 인덱스 주입

// ── Phase 3: 실행 ───────────────────────────
pub mod context_compact;  // 6. 컨텍스트 버짓 체크 + 자동 압축
pub mod llm_call;         // 7. LLM API 호출 (스트리밍)
pub mod tool_execute;     // 8. MCP 도구 실행 (LLMCall 후 복귀)

// ── Phase 4: 검증 ───────────────────────────
pub mod validate;         // 9. 독립 평가 LLM
pub mod decide;           // 10. 재시도/통과 결정

// ── Phase 5: 마무리 ──────────────────────────
pub mod memory_write;     // 11. 실행 결과 DB 저장
// complete는 agent_executor.rs에서 inline 처리

// ── 레거시 compat ────────────────────────────
pub mod init;             // 구 Init 단계 (Bootstrap+MemoryRead+ContextBuild 통합)
pub mod execute;          // 구 Execute 단계 (LLMCall+ToolExecute 통합 루프)
pub mod recover;          // 에러 복구 모듈 (cross-cutting)

// ── 유틸리티 ─────────────────────────────────
pub mod classify;         // 입력 복잡도 분류 → 자동 프리셋 선택
