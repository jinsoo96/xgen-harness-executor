"""
Decide strategies — s10_decide 용 Strategy 구현체

threshold: 기본 (도구 호출/점수/반복수 기반 판단) — Stage 내부 로직 사용
always_pass: 항상 complete (루프 없음, 1회 실행)
"""

from ..interfaces import Strategy


class ThresholdDecide(Strategy):
    """기본 판단 전략 — Stage 내부 하드코딩 로직 사용 (마커 역할)"""

    @property
    def name(self) -> str:
        return "threshold"

    @property
    def description(self) -> str:
        return "도구 호출 + 점수 기반 판단 (기본)"


class AlwaysPassDecide(Strategy):
    """항상 complete — 에이전트 루프 없이 1회 실행"""

    @property
    def name(self) -> str:
        return "always_pass"

    @property
    def description(self) -> str:
        return "항상 완료 (루프 없음)"
