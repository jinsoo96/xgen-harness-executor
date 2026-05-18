"""Cluster sync — Python HarnessConfig ↔ xgen-workflow DB row 양방향 통로 (v1.13).

순방향 — 이미 가능:
    cluster `GET /api/agentflow/harness/workflows/{id}` → workflow_data["harness_config"]
    → `HarnessConfig.from_workflow(harness_config, workflow_data)`

역방향 — 본 모듈:
    Python `HarnessConfig` 인스턴스 → `config.to_workflow_data(...)` → cluster
    `POST /api/agentflow/harness/workflows` body 그대로.

사용:
    from xgen_harness import HarnessConfig
    from xgen_harness.integrations import register_to_cluster

    config = HarnessConfig(provider="openai", system_prompt="...")
    result = await register_to_cluster(
        config,
        cluster_url="http://localhost:8023",
        workflow_id="wf_my_agent",
        workflow_name="My Agent",
        auth_token="...",         # 또는 internal_key
    )
    print(result["created"], result["workflow_id"])

API 엔드포인트는 이식측 (xgen-workflow) 의 표준 라우트 그대로 — 별도 endpoint
설계 없이 기존 `/workflows` CRUD 만 호출. cluster 코드 무침범.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from ..core.config import HarnessConfig


class ClusterSyncError(RuntimeError):
    """cluster API 호출 실패 (HTTP non-2xx / network)."""


_DEFAULT_BASE = "/api/agentflow/harness"


def _resolve_url(cluster_url: Optional[str]) -> str:
    raw = (cluster_url or os.environ.get("XGEN_CLUSTER_URL") or "").rstrip("/")
    if not raw:
        raise ClusterSyncError(
            "cluster_url 미지정 — 인자 또는 env XGEN_CLUSTER_URL 박으세요."
        )
    return raw


def _build_headers(
    *,
    auth_token: Optional[str],
    internal_key: Optional[str],
    user_id: Optional[str],
    extra_headers: Optional[dict[str, str]],
) -> dict[str, str]:
    headers: dict[str, str] = {"content-type": "application/json"}
    # Bearer JWT (사용자 로그인 토큰) 우선
    token = auth_token or os.environ.get("XGEN_AUTH_TOKEN")
    if token:
        headers["authorization"] = f"Bearer {token}"
    # internal_key 대안 (dev/script 용)
    key = internal_key or os.environ.get("XGEN_INTERNAL_API_KEY")
    if key:
        headers["x-api-key"] = key
    # user_id (internal_key 동반 시 필수)
    uid = user_id or os.environ.get("XGEN_USER_ID")
    if uid:
        headers["x-user-id"] = str(uid)
    if extra_headers:
        for k, v in extra_headers.items():
            if v is not None:
                headers[k.lower()] = str(v)
    return headers


async def register_to_cluster(
    config: HarnessConfig,
    *,
    cluster_url: Optional[str] = None,
    workflow_id: str,
    workflow_name: str,
    auth_token: Optional[str] = None,
    internal_key: Optional[str] = None,
    user_id: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Python ``HarnessConfig`` → cluster DB ``workflow_meta`` row 등록.

    내부적으로 ``POST /api/agentflow/harness/workflows`` 호출. 같은 workflow_id 가
    이미 있으면 cluster 가 409 반환 → ``ClusterSyncError`` 로 raise.

    인증: ``auth_token`` (Bearer JWT) 또는 ``internal_key`` (X-API-Key) 둘 중 하나.
    env ``XGEN_AUTH_TOKEN`` / ``XGEN_INTERNAL_API_KEY`` / ``XGEN_USER_ID`` 도 인입.

    Returns:
        cluster 응답 dict: ``{"workflow_id": ..., "workflow_name": ..., "created": True}``.
    """
    base = _resolve_url(cluster_url)
    body = {
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "workflow_data": config.to_workflow_data(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
        ),
    }
    headers = _build_headers(
        auth_token=auth_token,
        internal_key=internal_key,
        user_id=user_id,
        extra_headers=extra_headers,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base}{_DEFAULT_BASE}/workflows", json=body, headers=headers)
    if resp.status_code >= 300:
        raise ClusterSyncError(
            f"register_to_cluster HTTP {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


async def fetch_from_cluster(
    workflow_id: str,
    *,
    cluster_url: Optional[str] = None,
    auth_token: Optional[str] = None,
    internal_key: Optional[str] = None,
    user_id: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
) -> HarnessConfig:
    """cluster ``workflow_meta`` row → Python ``HarnessConfig`` 인스턴스.

    내부적으로 ``GET /api/agentflow/harness/workflows/{workflow_id}`` 호출.

    Returns:
        ``HarnessConfig`` — 응답의 harness_config + workflow_data 로 from_workflow.
    """
    base = _resolve_url(cluster_url)
    headers = _build_headers(
        auth_token=auth_token,
        internal_key=internal_key,
        user_id=user_id,
        extra_headers=extra_headers,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{base}{_DEFAULT_BASE}/workflows/{workflow_id}",
            headers=headers,
        )
    if resp.status_code >= 300:
        raise ClusterSyncError(
            f"fetch_from_cluster HTTP {resp.status_code}: {resp.text[:500]}"
        )
    data = resp.json()
    # cluster 응답 schema — {workflow_id, workflow_name, workflow_data: {...}}
    wd = data.get("workflow_data") or {}
    if not isinstance(wd, dict):
        raise ClusterSyncError("cluster 응답에 workflow_data dict 없음")
    hc = wd.get("harness_config") or {}
    return HarnessConfig.from_workflow(hc, wd)
