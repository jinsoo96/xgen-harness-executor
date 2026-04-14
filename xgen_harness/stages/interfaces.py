"""
스테이지 Strategy 인터페이스 계약

Stage×Strategy 이중 추상화를 Python ABC로 구현.
각 스테이지는 1개 이상의 Strategy 슬롯을 가지고,
슬롯마다 갈아끼울 수 있는 구현체를 인스턴스로 보유.

xgen-workflow 이식 시 고려사항:
- ToolRouter: MCP는 xgen-mcp-station HTTP, Node Bridge는 xgen-workflow 내부
- EvaluationStrategy: 독립 LLM 호출 → 프로바이더 인터페이스 재사용
- RetryStrategy: 429/529 재시도 → 프로바이더별 다를 수 있음
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공통 기반
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Strategy(ABC):
    """모든 Strategy의 기반 인터페이스."""

    @property
    @abstractmethod
    def name(self) -> str:
        """구현체 이름 (예: 'anthropic', 'sequential', 'llm_judge')"""
        ...

    @property
    def description(self) -> str:
        """UI에 표시할 설명"""
        return ""

    def configure(self, config: dict[str, Any]) -> None:
        """런타임 설정 주입 (stage_params에서 호출)"""
        pass


@dataclass
class StrategySlot:
    """Strategy 슬롯 메타데이터 — UI의 드롭다운 하나에 대응"""
    slot_name: str              # "provider", "retry", "executor"
    current_impl: str           # 현재 선택된 구현체 이름
    available_impls: list[str]  # 선택 가능한 구현체 목록
    description: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S07 LLM — 재시도 전략
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RetryStrategy(Strategy):
    """LLM 호출 재시도 전략 인터페이스"""

    @abstractmethod
    def should_retry(self, error: Exception, attempt: int) -> bool:
        """이 에러에 대해 재시도할지 결정"""
        ...

    @abstractmethod
    def get_delay(self, attempt: int) -> float:
        """n번째 재시도 전 대기 시간 (초)"""
        ...

    @property
    @abstractmethod
    def max_retries(self) -> int:
        ...


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S08 Execute — 도구 라우팅/실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class ToolResult:
    """도구 실행 결과 — 모든 도구 라우터가 이 타입을 반환"""
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolRouter(Strategy):
    """도구 라우팅 인터페이스.

    xgen-workflow 이식 시:
    - MCP 도구: xgen-mcp-station HTTP → MCPToolRouter
    - Node Bridge 도구: xgen-workflow 내부 → NodeBridgeRouter
    - 빌트인 도구: discover_tools 등 → BuiltinRouter
    """

    @abstractmethod
    async def route(self, tool_name: str, tool_input: dict) -> ToolResult:
        """도구 이름으로 라우팅하여 실행"""
        ...

    @abstractmethod
    async def list_available(self) -> list[dict[str, str]]:
        """사용 가능한 도구 목록 반환 [{name, description}]"""
        ...


class ToolExecutor(Strategy):
    """도구 실행 전략 인터페이스 — 순차/병렬 선택"""

    @abstractmethod
    async def execute_all(
        self,
        tool_calls: list[dict],
        router: ToolRouter,
    ) -> list[tuple[str, ToolResult]]:
        """tool_calls를 실행하고 [(tool_use_id, ToolResult)] 반환"""
        ...


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S09 Validate — 평가/검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class EvaluationResult:
    """평가 결과 — 모든 EvaluationStrategy가 이 타입을 반환"""
    passed: bool
    score: float                     # 0.0 ~ 1.0
    feedback: str = ""
    verdict: str = "pass"            # "pass" | "retry" | "fail"
    criteria: dict[str, float] = field(default_factory=dict)  # {"relevance": 0.8, ...}


class EvaluationStrategy(Strategy):
    """응답 품질 평가 전략 인터페이스.

    구현체:
    - LLMJudge: 독립 LLM 호출로 4가지 기준 평가 (기본)
    - RuleBased: 길이/키워드/정규식 규칙 (빠름, LLM 비용 없음)
    - NoValidation: 항상 통과 (검증 불필요 시)
    """

    @abstractmethod
    async def evaluate(
        self,
        user_input: str,
        assistant_response: str,
        context: Optional[dict] = None,
    ) -> EvaluationResult:
        ...


class QualityScorer(Strategy):
    """가중평균 점수 계산기 인터페이스"""

    @abstractmethod
    def score(self, criteria: dict[str, float]) -> float:
        """개별 기준 점수 → 종합 점수"""
        ...


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S04 Tool Index — 도구 디스커버리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ToolDiscoveryStrategy(Strategy):
    """도구 디스커버리 전략 인터페이스.

    구현체:
    - ProgressiveDisclosure: 메타데이터만 → discover_tools → 실행 (기본)
    - EagerLoad: 모든 도구 스키마 즉시 로드
    - NoDiscovery: 도구 인덱싱 비활성화
    """

    @abstractmethod
    async def discover(
        self,
        tool_definitions: list[dict],
        state: Any,
    ) -> tuple[list[dict], list[dict]]:
        """(tool_index, augmented_tool_definitions) 반환.

        tool_index: UI/프롬프트용 메타데이터 [{name, description, category}]
        augmented_tool_definitions: discover_tools 등 추가된 도구 정의
        """
        ...


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S06 Context — 컨텍스트 압축
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ContextCompactor(Strategy):
    """컨텍스트 압축 전략 인터페이스.

    구현체:
    - TokenBudget: 토큰 예산 기반 3단계 압축 (기본)
    - SlidingWindow: 최근 N개 메시지만 유지
    - Summarize: LLM으로 이전 대화 요약
    """

    @abstractmethod
    async def compact(
        self,
        messages: list[dict],
        system_prompt: str,
        budget_tokens: int,
        max_tokens: int,
    ) -> tuple[list[dict], str, bool]:
        """(compacted_messages, compacted_system_prompt, was_compacted) 반환"""
        ...
