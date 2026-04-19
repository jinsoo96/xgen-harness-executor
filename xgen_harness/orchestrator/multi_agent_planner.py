"""MultiAgentPlannerStage — 's05_plan' 슬롯의 'multi_agent' artifact.

흐름:
1. ComplexityDetector 가 escalate 판정.
2. escalate=False 면 즉시 bypass → 단일 에이전트 흐름 그대로.
3. escalate=True 면 RAG 컬렉션 / 인텐트 절 별로 SubAgent N 개를 만들고
   기존 DAGOrchestrator 로 병렬 실행.
4. 각 sub-agent 출력을 한 system_prompt 부록으로 묶어서 state 에 주입.
   → 본 파이프라인의 s07_llm 이 자연스럽게 종합 답변을 만든다.

캔버스 데이터 의존 0. harness_config + state 만으로 동작.
"""

from __future__ import annotations

import logging

from ..core.config import HarnessConfig
from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..events.emitter import EventEmitter
from ..events.types import StageSubstepEvent
from .complexity import ComplexityDetector
from .dag import AgentNode, DAGEdge, DAGOrchestrator

logger = logging.getLogger("harness.orchestrator.planner")


class MultiAgentPlannerStage(Stage):
    """s05_plan 슬롯의 multi_agent artifact — 자동 분기 + 종합."""

    @property
    def stage_id(self) -> str:
        return "s05_plan"

    @property
    def order(self) -> int:
        return 5

    def should_bypass(self, state: PipelineState) -> bool:
        return state.loop_iteration > 1

    async def execute(self, state: PipelineState) -> dict:
        config = state.config
        rag_collections: list[str] = self._collect_rag_collections(state)
        capabilities: list[str] = list(getattr(config, "capabilities", []) or []) if config else []
        tool_count = len(state.tool_definitions or [])

        detector = ComplexityDetector(
            long_input_chars=int(self.get_param("long_input_chars", state, 280)),
            rag_threshold=int(self.get_param("rag_threshold", state, 2)),
            capability_threshold=int(self.get_param("capability_threshold", state, 2)),
            tool_threshold=int(self.get_param("tool_threshold", state, 8)),
            score_to_escalate=int(self.get_param("score_to_escalate", state, 2)),
        )
        verdict = detector.evaluate(
            user_input=state.user_input or "",
            rag_collections=rag_collections,
            capabilities=capabilities,
            tool_count=tool_count,
        )

        await state.emit_verbose(StageSubstepEvent(
            stage_id=self.stage_id,
            substep="complexity_evaluated",
            meta={
                "escalate": verdict.escalate,
                "score": verdict.score,
                "signals": verdict.signals,
                "reasons": verdict.reasons,
            },
        ))

        if not verdict.escalate:
            return {
                "multi_agent": False,
                "complexity_score": verdict.score,
                "reasons": verdict.reasons,
            }

        # ── escalate: sub-agent DAG 구성 ──
        sub_agents = self._build_sub_agents(state, rag_collections, config)
        if not sub_agents:
            return {
                "multi_agent": False,
                "complexity_score": verdict.score,
                "skipped": "no_subtasks",
            }

        emitter = state.event_emitter or EventEmitter()
        orchestrator = DAGOrchestrator(emitter)
        for node in sub_agents:
            orchestrator.add_node(node)
        # 모든 sub-agent → 가상 aggregator 단계는 만들지 않음 (병렬 fan-out only).
        # 의존성 추가가 필요하면 fan_out_strategy 별로 add_edge.

        await state.emit_verbose(StageSubstepEvent(
            stage_id=self.stage_id,
            substep="multi_agent_run_start",
            meta={"sub_agent_count": len(sub_agents)},
        ))

        result = await orchestrator.run(state.user_input or "")

        # 결과 종합: 본 파이프라인의 system_prompt 에 sub-agent 결과를 부록으로 추가.
        # s07_llm 이 이걸 컨텍스트로 종합 답변을 생성.
        appendix_parts = ["\n\n<sub_agent_results>"]
        for node in sub_agents:
            r = result.results.get(node.node_id)
            if not r:
                continue
            appendix_parts.append(
                f"<agent name=\"{node.name}\" success=\"{r.success}\">"
                f"{r.output[:4000]}"
                f"</agent>"
            )
        appendix_parts.append("</sub_agent_results>")
        state.system_prompt = (state.system_prompt or "") + "\n".join(appendix_parts)

        await state.emit_verbose(StageSubstepEvent(
            stage_id=self.stage_id,
            substep="multi_agent_run_complete",
            meta={
                "duration_ms": result.total_duration_ms,
                "tokens": result.total_tokens,
                "cost_usd": result.total_cost_usd,
                "success": result.success,
            },
        ))

        logger.info(
            "[MultiAgentPlanner] escalated: %d sub-agents, %dms, %d tokens",
            len(sub_agents), result.total_duration_ms, result.total_tokens,
        )
        return {
            "multi_agent": True,
            "complexity_score": verdict.score,
            "reasons": verdict.reasons,
            "sub_agent_count": len(sub_agents),
            "duration_ms": result.total_duration_ms,
            "tokens": result.total_tokens,
            "cost_usd": result.total_cost_usd,
        }

    def _collect_rag_collections(self, state: PipelineState) -> list[str]:
        """RAG 컬렉션은 s04_tool_index 와 s06_context 두 곳의 stage_params 에 흩어짐.
        둘 다 모아서 dedupe."""
        config = state.config
        seen: list[str] = []
        if not config:
            return seen
        for sid in ("s04_tool_index", "s06_context"):
            params = config.stage_params.get(sid, {}) or {}
            for c in params.get("rag_collections", []) or []:
                if c and c not in seen:
                    seen.append(c)
        # state.metadata fallback (Builder 흐름)
        for c in state.metadata.get("rag_collections", []) or []:
            name = c.get("collection") if isinstance(c, dict) else c
            if name and name not in seen:
                seen.append(name)
        return seen

    def _build_sub_agents(
        self,
        state: PipelineState,
        rag_collections: list[str],
        base_config,
    ) -> list[AgentNode]:
        """fan-out 전략: per-RAG-collection (문서 RAG 가 주 use case)."""
        strategy = self.get_param("fan_out", state, "per_rag_collection")
        if not base_config:
            return []
        provider = getattr(base_config, "provider", "openai")
        model = getattr(base_config, "model", "")
        temperature = getattr(base_config, "temperature", 0.7)

        agents: list[AgentNode] = []
        if strategy == "per_rag_collection" and rag_collections:
            for col in rag_collections:
                sub_cfg = HarnessConfig(
                    preset="standard",
                    provider=provider,
                    model=model,
                    temperature=temperature,
                    system_prompt=(
                        f"너는 '{col}' 컬렉션에 대해서만 답하는 전문 sub-agent. "
                        "주어진 사용자 질의에서 이 컬렉션과 관련된 부분만 응답. 1500자 이내."
                    ),
                    # sub-agent 는 멀티에이전트 재진입 방지 — 디폴트 plan 으로 강제
                    artifacts={"s05_plan": "default"},
                    # RAG 컬렉션은 stage_params 로 주입 (s04/s06 둘 다 인지)
                    stage_params={
                        "s04_tool_index": {"rag_collections": [col]},
                        "s06_context": {"rag_collections": [col]},
                    },
                )
                agents.append(AgentNode(
                    node_id=f"sub_{col}",
                    name=f"RAG[{col}]",
                    config=sub_cfg,
                ))
        # 다른 전략 (per_intent, per_capability) 은 추후 추가 가능 (entry_points 로 외부 등록)
        return agents

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("per_rag_collection", "RAG 컬렉션 별로 sub-agent fan-out", is_default=True),
        ]
