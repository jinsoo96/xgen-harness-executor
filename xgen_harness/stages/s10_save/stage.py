"""
S10 Save — 실행 결과 DB 저장 (v0.14.0 번호 시프트: s11_save → s10_save)

harness_execution_log 테이블에 실행 결과 기록.
DB 연결이 없으면 graceful skip.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState

logger = logging.getLogger("harness.stage.save")


class SaveStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s10_save"

    @property
    def order(self) -> int:
        return 10

    async def execute(self, state: PipelineState) -> dict:
        # v0.26.0 — strategy="noop" 분기 추가 (D7 fix).
        # 이전엔 save_enabled toggle 만이 실 wiring 이고 noop 라벨은 분기 코드 없어
        # 사용자 거짓말이었음. 이제 strategy=="noop" 도 동일하게 skip.
        strategy_name = (self.get_param("strategy", state, None) or "").strip().lower()
        if strategy_name == "noop":
            logger.info("[Save] strategy=noop, skipping")
            return {"saved": False, "reason": "strategy=noop"}

        # save_enabled가 False이면 저장 건너뛰기
        if not self.get_param("save_enabled", state, True):
            logger.info("[Save] save_enabled=False, skipping")
            return {"saved": False, "reason": "save_enabled=False"}

        # DB 저장은 xgen-workflow의 execution_io와 호환
        # 여기서는 실행 결과를 metadata에 정리
        table_name = self.get_param("table_name", state, "harness_execution_log")
        # v0.26.3 — 실 DB schema 와 컬럼명 정합:
        # harness_execution_log 테이블은 input_text / output_text 컬럼이고
        # input_data / output_data 컬럼이 없음. 라이브 검증으로 발견:
        #   PostgreSQL: column "input_data" of relation "harness_execution_log" does not exist
        # 이전엔 dict 로 넣었지만 column 이 text 타입이라 평문 텍스트로 변경.
        record = {
            "execution_id": state.execution_id,
            "workflow_id": state.workflow_id,
            "workflow_name": state.workflow_name,
            "user_id": state.user_id,
            "interaction_id": state.interaction_id,
            "status": "completed",
            "input_text": (state.user_input or "")[:5000],
            "output_text": (state.final_output or state.last_assistant_text or "")[:50000],
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
        state.metadata["execution_table_name"] = table_name

        # ServiceProvider.database 가 주입되면 직접 저장. 없으면 graceful skip
        # (어댑터 레벨에서 다른 경로 — 예: harness.py 의 _save_execution_record — 로 처리 가능).
        inserted_id: Optional[int] = None
        services = state.metadata.get("services")
        if services and getattr(services, "database", None):
            try:
                inserted_id = await services.database.insert_record(table_name, record)
            except Exception as e:
                logger.warning("[Save] DB insert 실패 (graceful skip): %s", e)

        logger.info(
            "[Save] Execution record prepared: %s (table=%s, inserted_id=%s)",
            state.execution_id, table_name, inserted_id,
        )
        return {
            "saved": True,
            "execution_id": state.execution_id,
            "table_name": table_name,
            "inserted_id": inserted_id,
        }

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "harness_execution_log DB 저장", is_default=True),
            StrategyInfo("noop", "저장 비활성화"),
        ]
