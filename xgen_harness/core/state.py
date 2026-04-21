"""
PipelineState — 파이프라인 실행 상태

모든 스테이지가 공유하는 뮤터블 상태 객체.
하네스 파이프라인 실행 상태.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..events.emitter import EventEmitter
    from ..providers.base import LLMProvider
    from .config import HarnessConfig


@dataclass
class TokenUsage:
    """토큰 사용량 추적"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __iadd__(self, other: "TokenUsage") -> "TokenUsage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens
        return self


@dataclass
class PipelineState:
    """파이프라인 실행 상태 — 모든 스테이지가 읽고 쓴다"""

    # --- 실행 식별자 ---
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    workflow_name: str = ""
    interaction_id: str = ""
    user_id: str = ""

    # --- 설정 (초기화 시 주입) ---
    config: Optional["HarnessConfig"] = None

    # --- 입력 ---
    user_input: str = ""
    attached_files: list[dict[str, Any]] = field(default_factory=list)

    # --- LLM ---
    provider: Optional["LLMProvider"] = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""

    # --- 도구 ---
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)   # Anthropic API 포맷
    tool_index: list[dict[str, str]] = field(default_factory=list)         # Level 1 메타데이터
    tool_schemas: dict[str, dict] = field(default_factory=dict)            # Level 2 (on-demand)
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    # --- 메모리 ---
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    previous_results: list[str] = field(default_factory=list)

    # --- RAG ---
    rag_context: str = ""

    # --- 루프 제어 ---
    loop_iteration: int = 0
    loop_decision: str = "continue"    # "continue" | "complete" | "retry"

    # --- 검증 ---
    validation_score: Optional[float] = None
    validation_feedback: str = ""
    retry_count: int = 0

    # --- 토큰/비용 ---
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    turn_usages: list[TokenUsage] = field(default_factory=list)
    cost_usd: float = 0.0
    llm_call_count: int = 0
    tools_executed_count: int = 0

    # --- 타이밍 ---
    start_time: float = field(default_factory=time.time)
    stage_timings: dict[str, float] = field(default_factory=dict)

    # --- 출력 ---
    final_output: str = ""
    last_assistant_text: str = ""

    # --- 이벤트 ---
    event_emitter: Optional["EventEmitter"] = None

    # --- 워크플로우 원본 데이터 (연동용) ---
    workflow_data: dict[str, Any] = field(default_factory=dict)

    # --- 메타데이터 ---
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- Progressive Disclosure 저장소 ---
    # 큰 리소스의 원본을 보존하면서 messages 에는 preview 만 노출하는 패턴의 백업 저장소.
    # Level 1 (preview in messages) → Level 2 (fetch_pd(kind, id) 빌트인으로 원본 조회) → Level 3 (본문 삽입).
    # kind: "tool_result" | "rag" | "history" | "db_schema" | "gallery" | ...
    # id:   kind 별 식별자 (tool_use_id / chunk_index / turn_index / table_name / ...)
    # value: {"preview": str, "full": str, "meta": dict} — preview 는 이미 messages 에 있고,
    #                                                     full 이 복원용 원본.
    pd_stores: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    # === 헬퍼 메서드 ===

    def add_message(self, role: str, content: Any) -> None:
        """메시지 추가 (Anthropic API 포맷)"""
        self.messages.append({"role": role, "content": content})

    def add_tool_result(self, tool_use_id: str, content: str, is_error: bool = False) -> None:
        """도구 결과를 user 메시지로 추가"""
        result_block = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            result_block["is_error"] = True
        self.tool_results.append(result_block)

    def flush_tool_results(self) -> None:
        """축적된 도구 결과를 user 메시지로 밀어넣고 클리어"""
        if self.tool_results:
            self.add_message("user", self.tool_results.copy())
            self.tool_results.clear()
            self.pending_tool_calls.clear()

    # --- Progressive Disclosure 헬퍼 ---
    def pd_store(
        self,
        kind: str,
        resource_id: str,
        preview: str,
        full: str,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        """PD 리소스 등록. messages 에는 preview 만 노출, 원본은 pd_stores 에 보존.

        kind: "tool_result" / "rag" / "history" / "db_schema" / "gallery" 등 자유 문자열.
        resource_id: kind 내부에서 고유한 식별자 (tool_use_id / chunk index / turn idx ...).
        preview: messages 경로로 흘릴 경량 요약.
        full: fetch_pd 빌트인이 돌려줄 원본.
        meta: 진단용 메타 (chars, source, truncated_at 등).
        """
        bucket = self.pd_stores.setdefault(kind, {})
        bucket[resource_id] = {
            "preview": preview,
            "full": full,
            "meta": dict(meta or {}),
        }

    def pd_fetch(self, kind: str, resource_id: str) -> Optional[dict[str, Any]]:
        """PD 리소스 조회. 없으면 None."""
        bucket = self.pd_stores.get(kind)
        if not bucket:
            return None
        return bucket.get(resource_id)

    def pd_list(self, kind: str) -> list[str]:
        """해당 kind 의 보존 리소스 id 목록."""
        return list(self.pd_stores.get(kind, {}).keys())

    async def emit_verbose(self, event: Any) -> None:
        """HarnessConfig.verbose_events=True 시에만 이벤트 발행.

        Stage/어댑터가 세밀한 관찰 이벤트(CapabilityBindEvent, StageSubstepEvent,
        RetryEvent 등)를 뿌릴 때 이 한 줄만 호출. 플래그가 꺼져 있으면 no-op —
        기본 경로에 성능/출력 영향 0.
        """
        if self.event_emitter is None:
            return
        if not (self.config and getattr(self.config, "verbose_events", False)):
            return
        try:
            await self.event_emitter.emit(event)
        except Exception as e:
            # v0.11.21 — 관찰 이벤트 실패는 실행 흐름에 영향 없음. 단 완전 swallow 는 디버깅
            # 난도를 올리므로 로거에 흔적. logging 모듈은 모듈 로드 시점에만 필요.
            import logging as _logging
            _logging.getLogger("harness.state").debug("emit_verbose suppressed: %s", e)

    @property
    def elapsed_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)

    @property
    def is_over_budget(self) -> bool:
        if self.config and self.config.cost_budget_usd:
            return self.cost_usd > self.config.cost_budget_usd
        return False

    @property
    def is_over_iterations(self) -> bool:
        if self.config:
            return self.loop_iteration >= self.config.max_iterations
        return self.loop_iteration >= 10
