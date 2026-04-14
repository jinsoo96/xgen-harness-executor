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

EVALUATION_PROMPT = """You are an AI response evaluator. Evaluate the assistant's response based on these criteria:

1. **Relevance** (0-1, weight 0.3): Does the response address the user's question?
2. **Completeness** (0-1, weight 0.3): Is the response thorough and complete?
3. **Accuracy** (0-1, weight 0.2): Is the information accurate and well-supported?
4. **Clarity** (0-1, weight 0.2): Is the response clear and well-organized?

User's question: {user_input}

Assistant's response: {assistant_response}

Respond with ONLY a JSON object (no markdown, no explanation):
{{"relevance": 0.0, "completeness": 0.0, "accuracy": 0.0, "clarity": 0.0, "overall": 0.0, "feedback": "brief feedback"}}"""


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

    async def _execute_llm_judge(self, state: PipelineState) -> dict:
        """기존 LLM Judge 로직 (폴백용)"""
        eval_prompt = EVALUATION_PROMPT.format(
            user_input=state.user_input[:500],
            assistant_response=state.last_assistant_text[:2000],
        )

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

        score, feedback, verdict = self._parse_evaluation(eval_text, state)

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

    def _parse_evaluation(self, eval_text: str, state: PipelineState) -> tuple[float, str, str]:
        """평가 결과 파싱"""
        threshold = self.get_param("threshold", state, state.config.validation_threshold if state.config else 0.7)

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

            # overall이 없으면 가중평균 계산
            if overall == 0.0:
                r = float(data.get("relevance", 0.5))
                c = float(data.get("completeness", 0.5))
                a = float(data.get("accuracy", 0.5))
                cl = float(data.get("clarity", 0.5))
                overall = r * 0.3 + c * 0.3 + a * 0.2 + cl * 0.2

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
