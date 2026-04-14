"""
DAG Orchestrator — 멀티 에이전트 DAG 실행

워크플로우 간 움직임이 많아서 오케스트레이터가 필수.
각 노드 = 하네스 파이프라인 1개 (단일 에이전트).
엣지 = 이전 에이전트 출력 → 다음 에이전트 입력.

기존 xgen-workflow의 DAG 토폴로지 정렬 + 병렬 실행 패턴 참고.
AsyncWorkflowExecutor의 workflow_tracker.py 패턴 차용.

구조:
- AgentNode: 하네스 파이프라인 1개의 설정
- DAGEdge: 노드 간 데이터 흐름
- DAGOrchestrator: 토폴로지 정렬 → 레벨별 병렬 실행
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.config import HarnessConfig
from ..core.pipeline import Pipeline
from ..core.state import PipelineState
from ..events.emitter import EventEmitter
from ..events.types import (
    StageEnterEvent,
    StageExitEvent,
    DoneEvent,
    ErrorEvent,
    MetricsEvent,
)

logger = logging.getLogger("harness.orchestrator")


@dataclass
class AgentNode:
    """DAG의 노드 — 하네스 파이프라인 1개"""
    node_id: str
    name: str
    config: HarnessConfig
    system_prompt: str = ""
    # 입력 변환 함수: 이전 노드 출력들 → 이 노드의 user_input
    input_transformer: Optional[Callable[[dict[str, str]], str]] = None
    # 추가 컨텍스트 (RAG, 파일 등)
    rag_context: str = ""
    tool_definitions: list[dict] = field(default_factory=list)


@dataclass
class DAGEdge:
    """노드 간 연결"""
    source: str      # source node_id
    target: str      # target node_id
    label: str = ""  # 엣지 설명 (선택)


@dataclass
class NodeResult:
    """개별 노드 실행 결과"""
    node_id: str
    output: str
    success: bool
    duration_ms: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""


@dataclass
class DAGResult:
    """전체 DAG 실행 결과"""
    results: dict[str, NodeResult] = field(default_factory=dict)
    execution_order: list[list[str]] = field(default_factory=list)  # 레벨별 실행 순서
    total_duration_ms: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    success: bool = True
    final_output: str = ""  # 마지막 노드의 출력

    def get_output(self, node_id: str) -> str:
        """특정 노드의 출력 가져오기"""
        r = self.results.get(node_id)
        return r.output if r else ""


class DAGOrchestrator:
    """
    DAG 기반 멀티 에이전트 오케스트레이터.

    사용법:
        orch = DAGOrchestrator()
        orch.add_node(AgentNode(node_id="researcher", ...))
        orch.add_node(AgentNode(node_id="writer", ...))
        orch.add_edge(DAGEdge(source="researcher", target="writer"))
        result = await orch.run("사용자 질문")
    """

    def __init__(self, event_emitter: Optional[EventEmitter] = None):
        self._nodes: dict[str, AgentNode] = {}
        self._edges: list[DAGEdge] = []
        self._event_emitter = event_emitter or EventEmitter()

    def add_node(self, node: AgentNode) -> "DAGOrchestrator":
        self._nodes[node.node_id] = node
        return self

    def add_edge(self, edge: DAGEdge) -> "DAGOrchestrator":
        self._edges.append(edge)
        return self

    async def run(self, initial_input: str) -> DAGResult:
        """DAG 실행 — 토폴로지 정렬 → 레벨별 병렬 실행"""
        start = time.time()
        dag_result = DAGResult()

        # 1. 토폴로지 정렬 — 레벨별 그룹화
        levels = self._topological_levels()
        dag_result.execution_order = levels

        logger.info("[Orchestrator] DAG: %d nodes, %d levels", len(self._nodes), len(levels))
        for i, level in enumerate(levels):
            logger.info("[Orchestrator] Level %d: %s", i, level)

        # 2. 레벨별 실행 (같은 레벨은 병렬)
        node_outputs: dict[str, str] = {}

        for level_idx, level_nodes in enumerate(levels):
            # 오케스트레이터 이벤트
            await self._event_emitter.emit(StageEnterEvent(
                stage_id=f"orchestrator_level_{level_idx}",
                stage_name=f"레벨 {level_idx} 실행 ({', '.join(level_nodes)})",
                phase="orchestrator",
                step=level_idx + 1,
                total=len(levels),
            ))

            # 같은 레벨의 노드들은 병렬 실행
            tasks = []
            for node_id in level_nodes:
                node = self._nodes[node_id]
                # 입력 결정: 루트 노드 → initial_input, 나머지 → 이전 노드 출력
                predecessors = self._get_predecessors(node_id)
                if not predecessors:
                    user_input = initial_input
                elif node.input_transformer:
                    pred_outputs = {p: node_outputs.get(p, "") for p in predecessors}
                    user_input = node.input_transformer(pred_outputs)
                else:
                    # 기본: 이전 노드 출력을 합쳐서 전달
                    parts = []
                    for p in predecessors:
                        output = node_outputs.get(p, "")
                        if output:
                            parts.append(f"[{self._nodes[p].name}의 결과]\n{output}")
                    user_input = "\n\n---\n\n".join(parts) if parts else initial_input

                tasks.append(self._run_node(node, user_input))

            # 병렬 실행
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for node_id, result in zip(level_nodes, results):
                if isinstance(result, Exception):
                    nr = NodeResult(
                        node_id=node_id,
                        output="",
                        success=False,
                        error=str(result),
                    )
                    dag_result.success = False
                    logger.error("[Orchestrator] Node %s failed: %s", node_id, result)
                else:
                    nr = result
                    node_outputs[node_id] = nr.output

                dag_result.results[node_id] = nr
                dag_result.total_tokens += nr.tokens
                dag_result.total_cost_usd += nr.cost_usd

            await self._event_emitter.emit(StageExitEvent(
                stage_id=f"orchestrator_level_{level_idx}",
                stage_name=f"레벨 {level_idx} 완료",
                output={nid: dag_result.results[nid].success for nid in level_nodes},
                step=level_idx + 1,
                total=len(levels),
            ))

        # 3. 최종 결과 = 마지막 레벨의 출력 합치기
        if levels:
            final_parts = []
            for node_id in levels[-1]:
                output = node_outputs.get(node_id, "")
                if output:
                    if len(levels[-1]) > 1:
                        final_parts.append(f"[{self._nodes[node_id].name}]\n{output}")
                    else:
                        final_parts.append(output)
            dag_result.final_output = "\n\n".join(final_parts)

        dag_result.total_duration_ms = int((time.time() - start) * 1000)

        # 메트릭스 이벤트
        await self._event_emitter.emit(MetricsEvent(
            duration_ms=dag_result.total_duration_ms,
            total_tokens=dag_result.total_tokens,
            cost_usd=dag_result.total_cost_usd,
            model="orchestrator",
        ))

        await self._event_emitter.emit(DoneEvent(
            final_output=dag_result.final_output,
            success=dag_result.success,
        ))

        logger.info(
            "[Orchestrator] Done: %dms, %d tokens, $%.4f, success=%s",
            dag_result.total_duration_ms,
            dag_result.total_tokens,
            dag_result.total_cost_usd,
            dag_result.success,
        )
        return dag_result

    async def _run_node(self, node: AgentNode, user_input: str) -> NodeResult:
        """단일 에이전트 노드 실행"""
        t0 = time.time()
        logger.info("[Orchestrator] Running node: %s (%s)", node.node_id, node.name)

        config = node.config
        if node.system_prompt:
            config.system_prompt = node.system_prompt

        emitter = EventEmitter()
        pipeline = Pipeline.from_config(config, emitter)

        state = PipelineState(
            user_input=user_input,
            rag_context=node.rag_context,
            tool_definitions=node.tool_definitions,
        )

        # 파이프라인 실행 (이벤트는 소비하지 않음 — 메인 emitter에 전달)
        async def _forward_events():
            async for event in emitter.stream():
                # 메인 오케스트레이터 emitter로 전달 (node_id 태그 추가)
                if hasattr(event, "stage_name"):
                    event.stage_name = f"[{node.name}] {event.stage_name}"
                await self._event_emitter.emit(event)

        forward_task = asyncio.create_task(_forward_events())
        await pipeline.run(state)
        await forward_task

        elapsed = int((time.time() - t0) * 1000)
        return NodeResult(
            node_id=node.node_id,
            output=state.final_output or state.last_assistant_text,
            success=True,
            duration_ms=elapsed,
            tokens=state.token_usage.total,
            cost_usd=state.cost_usd,
        )

    def _topological_levels(self) -> list[list[str]]:
        """토폴로지 정렬 — 레벨별 그룹화 (Kahn's algorithm)"""
        in_degree: dict[str, int] = defaultdict(int)
        adj: dict[str, list[str]] = defaultdict(list)

        for node_id in self._nodes:
            in_degree[node_id] = 0

        for edge in self._edges:
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        # BFS
        queue = deque([n for n in self._nodes if in_degree[n] == 0])
        levels: list[list[str]] = []

        while queue:
            level = list(queue)
            levels.append(level)
            next_queue: deque[str] = deque()
            for node_id in level:
                for neighbor in adj[node_id]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_queue.append(neighbor)
            queue = next_queue

        return levels

    def _get_predecessors(self, node_id: str) -> list[str]:
        """노드의 선행 노드 목록"""
        return [e.source for e in self._edges if e.target == node_id]
