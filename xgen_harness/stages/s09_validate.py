"""
S09 Validate — 응답 품질 검증

독립 LLM 호출로 에이전트 응답을 평가.
평가 기준: 관련성(0.3) + 완전성(0.3) + 정확성(0.2) + 명확성(0.2)

full 프리셋에서만 활성화. 추가 LLM 호출 비용 발생.
"""

import json
import logging
from typing import Optional

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..events.types import EvaluationEvent
from ..providers.base import ProviderEventType

logger = logging.getLogger("harness.stage.validate")

ALL_CRITERIA = {
    "relevance": {"description": "Does the response address the user's question?", "weight": 0.3},
    "completeness": {"description": "Is the response thorough and complete?", "weight": 0.3},
    "accuracy": {"description": "Is the information accurate and well-supported?", "weight": 0.2},
    "clarity": {"description": "Is the response clear and well-organized?", "weight": 0.2},
}

EVALUATION_PROMPT_TEMPLATE = """You are an AI response evaluator. Evaluate the assistant's response based on these criteria:

{criteria_block}

User's question: {user_input}

Assistant's response: {assistant_response}

Respond with ONLY a JSON object (no markdown, no explanation):
{{{criteria_json_fields}, "overall": 0.0, "feedback": "brief feedback"}}"""


class ValidateStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s09_validate"

    @property
    def order(self) -> int:
        return 9

    def should_bypass(self, state: PipelineState) -> bool:
        # 텍스트 응답이 없으면 bypass
        return not state.last_assistant_text

    async def execute(self, state: PipelineState) -> dict:
        if not state.provider or not state.last_assistant_text:
            return {"bypassed": True, "reason": "no response to validate"}

        # ── Strategy 디스패치 ──
        strategy = self.resolve_strategy("evaluation", state, "llm_judge")
        if strategy:
            from ..stages.interfaces import EvaluationStrategy
            if isinstance(strategy, EvaluationStrategy):
                result = await strategy.evaluate(
                    user_input=state.user_input[:500],
                    assistant_response=state.last_assistant_text[:2000],
                    context={"provider": state.provider, "state": state},
                )
                state.validation_score = result.score
                state.validation_feedback = result.feedback

                if state.event_emitter:
                    await state.event_emitter.emit(EvaluationEvent(
                        score=result.score,
                        feedback=result.feedback,
                        verdict=result.verdict,
                    ))

                logger.info("[Validate] strategy=%s, score=%.2f, verdict=%s", strategy.name, result.score, result.verdict)
                return {"overall": result.score, "verdict": result.verdict, "feedback": result.feedback}

        # ── 폴백: 기존 하드코딩 로직 (strategy resolve 실패 시) ──
        return await self._execute_llm_judge(state)

    def _build_evaluation_prompt(self, state: PipelineState) -> tuple[str, list[str]]:
        """선택된 criteria로 평가 프롬프트 생성"""
        criteria = self.get_param("criteria", state, ["relevance", "completeness", "accuracy", "clarity"])
        if isinstance(criteria, str):
            criteria = [c.strip() for c in criteria.split(",")]

        # 유효한 criteria만 필터링
        selected = {k: v for k, v in ALL_CRITERIA.items() if k in criteria}
        if not selected:
            selected = ALL_CRITERIA  # 폴백: 전체 사용

        # 가중치 재정규화
        total_weight = sum(c["weight"] for c in selected.values())
        criteria_block = "\n".join(
            f"{i+1}. **{name.capitalize()}** (0-1, weight {info['weight']/total_weight:.2f}): {info['description']}"
            for i, (name, info) in enumerate(selected.items())
        )
        criteria_json_fields = ", ".join(f'"{name}": 0.0' for name in selected)

        prompt = EVALUATION_PROMPT_TEMPLATE.format(
            criteria_block=criteria_block,
            user_input=state.user_input[:500],
            assistant_response=state.last_assistant_text[:2000],
            criteria_json_fields=criteria_json_fields,
        )
        return prompt, list(selected.keys())

    async def _execute_llm_judge(self, state: PipelineState) -> dict:
        """기존 LLM Judge 로직 (폴백용)"""
        eval_prompt, selected_criteria = self._build_evaluation_prompt(state)

        eval_text = ""
        try:
            async for event in state.provider.chat(
                messages=[{"role": "user", "content": eval_prompt}],
                temperature=0.0,
                max_tokens=500,
                stream=False,
            ):
                if event.type == ProviderEventType.STOP:
                    eval_text = event.text
                elif event.type == ProviderEventType.TEXT_DELTA:
                    eval_text += event.text

            state.llm_call_count += 1
        except Exception as e:
            logger.warning("[Validate] Evaluation LLM call failed: %s", e)
            return {"bypassed": True, "reason": f"evaluation failed: {e}"}

        score, feedback, verdict = self._parse_evaluation(eval_text, state, selected_criteria)

        state.validation_score = score
        state.validation_feedback = feedback

        if state.event_emitter:
            await state.event_emitter.emit(EvaluationEvent(
                score=score,
                feedback=feedback,
                verdict=verdict,
            ))

        logger.info("[Validate] score=%.2f, verdict=%s, feedback=%s", score, verdict, feedback[:100])
        return {"overall": score, "verdict": verdict, "feedback": feedback}

    def _parse_evaluation(self, eval_text: str, state: PipelineState, selected_criteria: list[str] | None = None) -> tuple[float, str, str]:
        """평가 결과 파싱"""
        threshold = self.get_param("threshold", state, state.config.validation_threshold if state.config else 0.7)
        if selected_criteria is None:
            selected_criteria = list(ALL_CRITERIA.keys())

        try:
            # JSON 추출 (마크다운 코드블록 내부일 수 있음)
            text = eval_text.strip()
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}") + 1
                text = text[start:end]

            data = json.loads(text)
            overall = float(data.get("overall", 0.0))
            feedback = data.get("feedback", "")

            # overall이 없으면 선택된 criteria로 가중평균 계산
            if overall == 0.0:
                selected = {k: v for k, v in ALL_CRITERIA.items() if k in selected_criteria}
                if not selected:
                    selected = ALL_CRITERIA
                total_weight = sum(c["weight"] for c in selected.values())
                overall = sum(
                    float(data.get(name, 0.5)) * (info["weight"] / total_weight)
                    for name, info in selected.items()
                )

            verdict = "pass" if overall >= threshold else "retry"
            return overall, feedback, verdict

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("[Validate] Failed to parse evaluation: %s", e)
            return 0.7, "Evaluation parsing failed, assuming acceptable", "pass"

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("llm_judge", "독립 LLM으로 4가지 기준 평가", is_default=True),
            StrategyInfo("rule_based", "규칙 기반 평가 (길이, 키워드)"),
            StrategyInfo("none", "검증 비활성화"),
        ]
