"""
JudgeThenLoopStrategy — s08_decide 의 'judge_then_loop' strategy (v1.0).

구 s08_judge stage 격하 흡수:
  1. 독립 LLM 호출(또는 EvaluationStrategy)로 응답 평가
  2. 평가 점수 + Guard 체인 + 도구 호출 상태로 루프 결정 (threshold 와 동일)

박제 풀기:
  - ALL_CRITERIA 모듈 노출 + register_evaluation_criterion() 공개 API
  - EVALUATION_PROMPT_TEMPLATE 외부 등록 가능 (register_evaluation_prompt_template)
  - entry_points("xgen_harness.evaluation_criteria") 자동 발견

Strategy 결과:
  - 점수 ≥ threshold 이고 도구 호출 없으면 LOOP_COMPLETE
  - 점수 < threshold → LOOP_RETRY
  - Policy Gate 가 block 했으면 그 결정 존중
  - max_retries 초과 → LOOP_COMPLETE (강제 종료)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("harness.stage.decide.judge_then_loop")


# ─── 평가 기준 레지스트리 (박제 0) ───────────────────────────────
ALL_CRITERIA: dict[str, dict] = {
    "relevance":    {"description": "Does the response address the user's question?",     "weight": 0.3},
    "completeness": {"description": "Is the response thorough and complete?",              "weight": 0.3},
    "accuracy":     {"description": "Is the information accurate and well-supported?",     "weight": 0.2},
    "clarity":      {"description": "Is the response clear and well-organized?",           "weight": 0.2},
}


def register_evaluation_criterion(name: str, description: str, weight: float = 0.1) -> None:
    """외부 작업자가 평가 기준 추가하는 공개 API. UI 멀티셀렉터에 자동 노출."""
    ALL_CRITERIA[name] = {"description": description, "weight": float(weight)}


# ─── 평가 프롬프트 템플릿 (박제 0) ───────────────────────────────
EVALUATION_PROMPT_TEMPLATES: dict[str, str] = {
    "default": (
        "You are an AI response evaluator. Evaluate the assistant's response based on these criteria:\n\n"
        "{criteria_block}\n\n"
        "User's question: {user_input}\n\n"
        "Assistant's response: {assistant_response}\n\n"
        "Respond with ONLY a JSON object (no markdown, no explanation):\n"
        "{{{criteria_json_fields}, \"overall\": 0.0, \"feedback\": \"brief feedback\"}}"
    ),
}


def register_evaluation_prompt_template(name: str, template: str) -> None:
    """평가 프롬프트 템플릿 등록. stage_params.evaluation_prompt_template = name 으로 선택."""
    EVALUATION_PROMPT_TEMPLATES[name] = template


# 임계값 / 길이 cap 도 외부 조정 가능
JUDGE_DEFAULTS: dict[str, Any] = {
    "threshold": 0.7,
    "user_input_cap": 500,
    "response_cap": 2000,
}


def register_judge_defaults(**kwargs: Any) -> None:
    """judge 동작 기본값 override."""
    for k, v in kwargs.items():
        if k in JUDGE_DEFAULTS:
            JUDGE_DEFAULTS[k] = v


# ─── entry_points 자동 발견 ───────────────────────────────────────
_EVAL_CRITERIA_DISCOVERED = False


def _discover_evaluation_criteria_from_entry_points() -> None:
    """entry_points 그룹 ``xgen_harness.evaluation_criteria`` 자동 발견. idempotent."""
    global _EVAL_CRITERIA_DISCOVERED
    if _EVAL_CRITERIA_DISCOVERED:
        return
    _EVAL_CRITERIA_DISCOVERED = True
    try:
        from importlib.metadata import entry_points
    except Exception:
        return
    try:
        eps = entry_points()
        group = "xgen_harness.evaluation_criteria"
        items = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])  # type: ignore[arg-type]
        for ep in items:
            try:
                produced = ep.load()
                if callable(produced):
                    produced = produced()
                if isinstance(produced, dict):
                    name = produced.get("name", ep.name)
                    register_evaluation_criterion(
                        name,
                        produced.get("description", ""),
                        weight=float(produced.get("weight", 0.1)),
                    )
                elif isinstance(produced, list):
                    for item in produced:
                        if isinstance(item, dict) and item.get("name"):
                            register_evaluation_criterion(
                                item["name"],
                                item.get("description", ""),
                                weight=float(item.get("weight", 0.1)),
                            )
            except Exception as e:
                logger.warning("[evaluation_criteria] entry_point %s 로드 실패: %s", ep.name, e)
    except Exception as e:
        logger.debug("[evaluation_criteria] entry_points discovery 실패: %s", e)


_discover_evaluation_criteria_from_entry_points()


# ─── 평가 실행 ───────────────────────────────────────────────────
async def evaluate_response(state, get_param) -> dict:
    """LLM 또는 EvaluationStrategy 로 응답 평가 → 점수/feedback/verdict 반환.

    state.last_assistant_text 필수. 없으면 bypass dict 반환.
    """
    if not state.provider or not state.last_assistant_text:
        return {"bypassed": True, "reason": "no response to evaluate"}

    # EvaluationStrategy 가 등록돼 있으면 먼저 시도 (외부 평가 시스템 hook)
    eval_strategy_name = (get_param("evaluation_strategy", state, "llm_judge") or "").strip().lower()
    if eval_strategy_name == "none":
        return {"bypassed": True, "reason": "evaluation_strategy=none"}

    # 1) Strategy resolver 로 EvaluationStrategy 찾기 (있으면 사용)
    try:
        from ....stages.interfaces import EvaluationStrategy
        from ....core.strategy_resolver import resolve_strategy
        ev_strat = resolve_strategy("s08_decide", "evaluation", eval_strategy_name)
        if ev_strat is not None and isinstance(ev_strat, EvaluationStrategy):
            if hasattr(ev_strat, "set_provider"):
                try:
                    ev_strat.set_provider(state.provider)
                except Exception as e:
                    logger.debug("[judge] set_provider failed: %s", e)
            user_cap = int(get_param("user_input_cap", state, JUDGE_DEFAULTS["user_input_cap"]))
            resp_cap = int(get_param("response_cap", state, JUDGE_DEFAULTS["response_cap"]))
            result = await ev_strat.evaluate(
                user_input=(state.user_input or "")[:user_cap],
                assistant_response=state.last_assistant_text[:resp_cap],
                context={"provider": state.provider, "state": state},
            )
            state.validation_score = result.score
            state.validation_feedback = result.feedback
            return {"score": result.score, "feedback": result.feedback, "verdict": result.verdict}
    except ImportError:
        pass

    # 2) LLM judge 폴백 (raw aux_call)
    return await _llm_judge_fallback(state, get_param)


async def _llm_judge_fallback(state, get_param) -> dict:
    """raw LLM 호출로 평가. EvaluationStrategy 없을 때 폴백.

    v1.1.0 — config.judge_provider / judge_model 지원. 미지정 시 본문 LLM 재사용
    (backward compat). judge_provider 가 본문 provider 와 다르면 별도 provider
    인스턴스 띄워 호출 — "Judge 가 자기 답을 자기가 평가" 약점 회피.
    """
    eval_prompt, selected_criteria = _build_evaluation_prompt(state, get_param)

    # v1.0.7 — judge LLM 의 system prompt 사용자 override 지원. 미설정(None) 이면
    # provider default 그대로. 박제 0 — 엔진은 시스템 메시지 안 주입, 외부에서만.
    eval_system = get_param("evaluation_system_prompt", state, None)
    eval_system_str: str | None = (
        str(eval_system).strip() if isinstance(eval_system, str) and eval_system.strip() else None
    )

    # v1.1.0 — judge_model lookup. 빈 값이면 본문 LLM 재사용 (backward compat).
    # judge_provider 는 v1.1.0 에서 Pydantic 필드만 보존, 본문과 다른 provider 사용은
    # API 키 wiring 까지 정합 필요해서 v1.1.x 후속. 같은 provider 다른 model 만 우선.
    # v1.7.1 — judge_use_main=True 면 judge_model 박혀있어도 강제 본문 재사용
    # (사용자 UI chip "본문 재사용" 명시 의도 우선).
    config = getattr(state, "config", None)
    if bool(getattr(config, "judge_use_main", False)):
        judge_model_name = ""
    else:
        judge_model_name = (str(getattr(config, "judge_model", "") or "")).strip()

    try:
        from ....core.llm_call import aux_call
        eval_text = await aux_call(
            state, stage_id="s08_decide", prompt=eval_prompt,
            system=eval_system_str,
            model=judge_model_name or None,
        )
    except Exception as e:
        logger.warning("[judge] aux_call 실패: %s", e)
        return {"bypassed": True, "reason": f"evaluation failed: {e}"}

    score, feedback, verdict = _parse_evaluation(eval_text, selected_criteria, get_param, state)
    state.validation_score = score
    state.validation_feedback = feedback
    return {"score": score, "feedback": feedback, "verdict": verdict}


def _build_evaluation_prompt(state, get_param) -> tuple[str, list[str]]:
    user_cap = int(get_param("user_input_cap", state, JUDGE_DEFAULTS["user_input_cap"]))
    resp_cap = int(get_param("response_cap", state, JUDGE_DEFAULTS["response_cap"]))

    criteria = get_param("criteria", state, list(ALL_CRITERIA.keys()))
    if isinstance(criteria, str):
        criteria = [c.strip() for c in criteria.split(",")]

    selected = {k: v for k, v in ALL_CRITERIA.items() if k in criteria}
    if not selected:
        selected = ALL_CRITERIA

    total_weight = sum(c["weight"] for c in selected.values())
    criteria_block = "\n".join(
        f"{i+1}. **{name.capitalize()}** (0-1, weight {info['weight']/total_weight:.2f}): {info['description']}"
        for i, (name, info) in enumerate(selected.items())
    )
    criteria_json_fields = ", ".join(f'"{name}": 0.0' for name in selected)

    template_name = get_param("evaluation_prompt_template", state, "default")
    template = EVALUATION_PROMPT_TEMPLATES.get(template_name, EVALUATION_PROMPT_TEMPLATES["default"])

    prompt = template.format(
        criteria_block=criteria_block,
        user_input=(state.user_input or "")[:user_cap],
        assistant_response=state.last_assistant_text[:resp_cap],
        criteria_json_fields=criteria_json_fields,
    )
    return prompt, list(selected.keys())


def _parse_evaluation(eval_text: str, selected_criteria: list[str],
                      get_param, state) -> tuple[float, str, str]:
    threshold = float(get_param(
        "judge_threshold", state,
        get_param("threshold", state,
                  state.config.validation_threshold if state.config else JUDGE_DEFAULTS["threshold"])
    ))
    try:
        text = eval_text.strip()
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            text = text[start:end]
        data = json.loads(text)
        overall = float(data.get("overall", 0.0))
        feedback = data.get("feedback", "")

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
        logger.warning("[judge] parse 실패: %s", e)
        return JUDGE_DEFAULTS["threshold"], "Evaluation parsing failed, assuming acceptable", "pass"
