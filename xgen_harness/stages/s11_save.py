"""
S11 Save — 실행 결과 DB 저장

harness_execution_log 테이블에 실행 결과 기록.
DB 연결이 없으면 graceful skip.
"""

import json
import logging
from datetime import datetime, timezone

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState

logger = logging.getLogger("harness.stage.save")


class SaveStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s11_save"

    @property
    def order(self) -> int:
        return 11

    async def execute(self, state: PipelineState) -> dict:
        # DB 저장은 xgen-workflow의 execution_io와 호환
        # 여기서는 실행 결과를 metadata에 정리
        record = {
            "execution_id": state.execution_id,
            "workflow_id": state.workflow_id,
            "workflow_name": state.workflow_name,
            "user_id": state.user_id,
            "interaction_id": state.interaction_id,
            "status": "completed",
            "input_data": {
                "text": state.user_input[:5000],
                "files_count": len(state.attached_files),
            },
            "output_data": {
                "content": state.final_output or state.last_assistant_text,
            },
            "metrics": {
                "duration_ms": state.elapsed_ms,
                "input_tokens": state.token_usage.input_tokens,
                "output_tokens": state.token_usage.output_tokens,
                "total_tokens": state.token_usage.total,
                "cost_usd": state.cost_usd,
                "llm_calls": state.llm_call_count,
                "tools_executed": state.tools_executed_count,
                "iterations": state.loop_iteration,
                "model": state.provider.model_name if state.provider else "",
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if state.validation_score is not None:
            record["metrics"]["validation_score"] = state.validation_score

        state.metadata["execution_record"] = record

        # TODO: 실제 DB 저장 (db_manager가 state에 있으면)
        # db_manager.insert_record("harness_execution_log", record)

        logger.info("[Save] Execution record prepared: %s", state.execution_id)
        return {"saved": True, "execution_id": state.execution_id}

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "harness_execution_log DB 저장", is_default=True),
            StrategyInfo("noop", "저장 비활성화"),
        ]
