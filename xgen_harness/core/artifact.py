"""
Artifact 시스템 — Stage 구현체 갈아끼기

geny-harness 패턴:
  - Stage는 인터페이스 (뭘 받고 뭘 내보내는지)
  - Artifact는 구현체 (어떻게 실행하는지)
  - default Artifact를 복사해서 v2를 만들 수 있음
  - 새로 만든 Artifact는 등록/검증 후 사용

사용:
    # default Artifact 조회
    artifact = artifact_store.get("s07_llm", "default")

    # 복사해서 커스텀 Artifact 생성
    custom = artifact_store.clone("s07_llm", "default", "my_streaming_v2")
    custom.config["temperature"] = 0.3
    artifact_store.register(custom)

    # 워크플로우에서 Artifact 선택
    harness_config.artifacts = {"s07_llm": "my_streaming_v2"}
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Type

from .stage import Stage

logger = logging.getLogger("harness.artifact")


@dataclass
class ArtifactMeta:
    """Artifact 메타데이터 — DB/API 직렬화용"""
    stage_id: str
    artifact_name: str
    description: str = ""
    version: int = 1
    is_default: bool = False
    is_verified: bool = False       # 검증 완료 여부
    config: dict[str, Any] = field(default_factory=dict)  # Artifact별 설정
    created_at: float = field(default_factory=time.time)
    parent_artifact: str = ""       # 복사한 원본 (빈 문자열이면 원본)

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "artifact_name": self.artifact_name,
            "description": self.description,
            "version": self.version,
            "is_default": self.is_default,
            "is_verified": self.is_verified,
            "config": self.config,
            "created_at": self.created_at,
            "parent_artifact": self.parent_artifact,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ArtifactMeta":
        return cls(
            stage_id=data.get("stage_id", ""),
            artifact_name=data.get("artifact_name", ""),
            description=data.get("description", ""),
            version=data.get("version", 1),
            is_default=data.get("is_default", False),
            is_verified=data.get("is_verified", False),
            config=data.get("config", {}),
            created_at=data.get("created_at", time.time()),
            parent_artifact=data.get("parent_artifact", ""),
        )


class ArtifactStore:
    """Artifact 저장소 — 인메모리 + DB 연동

    default Artifact는 코드에서 등록.
    커스텀 Artifact는 UI에서 생성 → DB 저장 → 로드.
    """

    def __init__(self):
        # (stage_id, artifact_name) → ArtifactMeta
        self._meta: dict[tuple[str, str], ArtifactMeta] = {}
        # (stage_id, artifact_name) → Stage 클래스 (코드 기반)
        self._classes: dict[tuple[str, str], Type[Stage]] = {}

    def register_default(self, stage_id: str, stage_class: Type[Stage], description: str = "") -> None:
        """기본 Artifact 등록 (코드 기반, 항상 존재)"""
        key = (stage_id, "default")
        self._classes[key] = stage_class
        self._meta[key] = ArtifactMeta(
            stage_id=stage_id,
            artifact_name="default",
            description=description or f"Default {stage_id}",
            is_default=True,
            is_verified=True,
        )

    def register(self, meta: ArtifactMeta, stage_class: Optional[Type[Stage]] = None) -> None:
        """커스텀 Artifact 등록"""
        key = (meta.stage_id, meta.artifact_name)
        self._meta[key] = meta
        if stage_class:
            self._classes[key] = stage_class
        logger.info("Registered artifact: %s/%s (v%d)", meta.stage_id, meta.artifact_name, meta.version)

    def get(self, stage_id: str, artifact_name: str = "default") -> Optional[Stage]:
        """Artifact 인스턴스 반환"""
        key = (stage_id, artifact_name)
        cls = self._classes.get(key)
        if cls:
            return cls()
        # 폴백: default
        if artifact_name != "default":
            logger.warning("Artifact %s/%s not found, falling back to default", stage_id, artifact_name)
            return self.get(stage_id, "default")
        return None

    def get_meta(self, stage_id: str, artifact_name: str = "default") -> Optional[ArtifactMeta]:
        return self._meta.get((stage_id, artifact_name))

    def clone(self, stage_id: str, source_name: str, new_name: str) -> ArtifactMeta:
        """기존 Artifact를 복사해서 새 Artifact 생성.

        새로 만든 Artifact는 is_verified=False — 등록/검증 후 사용.
        """
        source = self._meta.get((stage_id, source_name))
        if not source:
            raise ValueError(f"Source artifact not found: {stage_id}/{source_name}")

        cloned = ArtifactMeta(
            stage_id=stage_id,
            artifact_name=new_name,
            description=f"Copy of {source_name}",
            version=1,
            is_default=False,
            is_verified=False,  # 새로 만든 건 검증 안 됨
            config=dict(source.config),  # 깊은 복사
            parent_artifact=source_name,
        )

        # 클래스도 복사 (같은 Stage 구현체 사용)
        source_cls = self._classes.get((stage_id, source_name))
        if source_cls:
            self._classes[(stage_id, new_name)] = source_cls

        self._meta[(stage_id, new_name)] = cloned
        logger.info("Cloned artifact: %s/%s → %s/%s", stage_id, source_name, stage_id, new_name)
        return cloned

    def list_artifacts(self, stage_id: str) -> list[ArtifactMeta]:
        """특정 스테이지의 모든 Artifact 메타 반환"""
        return [meta for (sid, _), meta in self._meta.items() if sid == stage_id]

    def list_all(self) -> dict[str, list[ArtifactMeta]]:
        """전체 Artifact 목록 (stage_id별 그룹)"""
        result: dict[str, list[ArtifactMeta]] = {}
        for (stage_id, _), meta in self._meta.items():
            result.setdefault(stage_id, []).append(meta)
        return result

    async def save_to_db(self, db_manager, stage_id: str, artifact_name: str) -> bool:
        """Artifact를 DB에 저장"""
        meta = self._meta.get((stage_id, artifact_name))
        if not meta or not db_manager:
            return False
        try:
            record = {
                "stage_id": stage_id,
                "artifact_name": artifact_name,
                "artifact_data": json.dumps(meta.to_dict(), ensure_ascii=False),
                "updated_at": time.time(),
            }
            db_manager.upsert_record(
                "harness_artifacts",
                {"stage_id": stage_id, "artifact_name": artifact_name},
                record,
            )
            return True
        except Exception as e:
            logger.error("Failed to save artifact %s/%s: %s", stage_id, artifact_name, e)
            return False

    async def load_from_db(self, db_manager) -> int:
        """DB에서 모든 커스텀 Artifact 로드"""
        if not db_manager:
            return 0
        try:
            records = db_manager.find_records_by_condition("harness_artifacts", {})
            count = 0
            for rec in records:
                data = json.loads(rec.get("artifact_data", "{}"))
                meta = ArtifactMeta.from_dict(data)
                key = (meta.stage_id, meta.artifact_name)
                if key not in self._meta:  # default 덮어쓰기 방지
                    self._meta[key] = meta
                    count += 1
            logger.info("Loaded %d custom artifacts from DB", count)
            return count
        except Exception as e:
            logger.warning("Failed to load artifacts from DB: %s", e)
            return 0
