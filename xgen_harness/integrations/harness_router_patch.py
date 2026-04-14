"""
harness_router.py 패치 — execute_via_harness() 교체 코드

이 파일의 execute_via_harness()를 xgen-workflow의 harness_router.py에 복사하면
Rust subprocess → Python Pipeline으로 전환됨.

변경 범위:
- execute_via_harness() 함수의 subprocess 부분만 교체
- 기존 입력 추출/RAG/파일/API키 로직은 100% 유지
- _convert_harness_event()도 그대로 사용 (하위 호환)

적용 방법:
1. harness_router.py 상단에 추가:
   from xgen_harness.integrations.workflow_bridge import execute_via_python_pipeline

2. execute_via_harness() 함수에서 subprocess 블록(line ~667~889)을 아래로 교체
"""

# === 아래 코드를 harness_router.py의 execute_via_harness() 내부에서
# === "# ── subprocess 직접 호출 모드" 주석부터 함수 끝까지 교체 ===

REPLACEMENT_CODE = '''
    # ── Python Pipeline 직접 호출 모드 ──
    # (Rust subprocess 대체)
    try:
        from xgen_harness.integrations.workflow_bridge import execute_via_python_pipeline
    except ImportError:
        # xgen_harness 미설치 시 기존 subprocess 폴백
        yield {"type": "error", "detail": "xgen-harness 라이브러리가 설치되지 않았습니다."}
        yield {"type": "__harness_fallback__"}
        return

    yield _make_log_event("INFO", f"[HARNESS] provider={provider}, model={model}, pipeline={harness_pipeline}")

    try:
        async for event in execute_via_python_pipeline(
            workflow_data=workflow_data,
            text=text,
            user_id=user_id,
            provider=provider,
            model=model,
            api_key=api_key,
            temperature=temperature,
            system_prompt=system_prompt,
            harness_pipeline=harness_pipeline,
            stages_list=stages_list,
            tools=tools,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            interaction_id=interaction_id,
            attached_files=attached_files,
            previous_results=previous_results,
            rag_context=rag_context,
            mcp_sessions=_collect_mcp_session_ids(workflow_data),
        ):
            yield event
    except Exception as e:
        logger.error(f"[HARNESS] Python Pipeline 실행 실패: {e}", exc_info=True)
        yield {"type": "error", "detail": f"하네스 실행 오류: {str(e)}"}
'''

# === 전체 교체된 execute_via_harness() 함수 ===
# 기존 로직(입력 추출~API키)은 그대로, subprocess 부분만 Python Pipeline으로 교체

FULL_FUNCTION = '''
async def execute_via_harness(
    workflow_data: dict,
    request_body,
    user_id: str,
    db_manager=None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    하네스 실행기로 워크플로우 실행을 위임하고 SSE 이벤트를 변환하여 yield.
    Python Pipeline 직접 호출 방식 (Rust subprocess 대체).
    """
    # ── 입력 텍스트 추출 (기존 로직 100% 유지) ──
    input_data = request_body.input_data
    if isinstance(input_data, dict):
        text = input_data.get("text", json.dumps(input_data, ensure_ascii=False))
    elif isinstance(input_data, list):
        text = json.dumps(input_data, ensure_ascii=False)
    else:
        text = str(input_data) if input_data else ""

    if not text.strip():
        text = _extract_input_from_workflow(workflow_data)
        if text:
            logger.info(f"[HARNESS] 워크플로우 노드에서 입력 추출: {len(text)}자")

    workflow_id = getattr(request_body, "workflow_id", "")

    # 병렬로 파일 추출 + 이전 결과 조회 (기존 로직)
    selected_files = getattr(request_body, "selected_files", None)
    attached_files_task = asyncio.ensure_future(_extract_attached_files(selected_files))
    previous_results_task = asyncio.ensure_future(_fetch_previous_results(db_manager, workflow_id))
    attached_files = await attached_files_task
    previous_results = await previous_results_task

    workflow_name = getattr(request_body, "workflow_name", "")
    interaction_id = getattr(request_body, "interaction_id", "")

    if not text.strip():
        yield {"type": "error", "detail": "입력 텍스트가 비어있습니다."}
        return

    logger.info(f"[HARNESS] 실행기 호출, workflow_id={workflow_id}, text_len={len(text)}")

    yield _make_log_event("INFO", "[HARNESS] 하네스 실행기 시작")

    # ── 하네스 설정 읽기 (기존 로직) ──
    hc = workflow_data.get("harness_config") or {}
    agent_config = None
    for node in workflow_data.get("nodes", []):
        cfg = _extract_agent_config(node)
        if cfg:
            agent_config = cfg
            break

    provider = hc.get("provider") or (agent_config or {}).get("provider", "anthropic")
    model = hc.get("model") or (agent_config or {}).get("model", "claude-sonnet-4-6")
    temperature = hc.get("temperature") if hc.get("temperature") is not None else (agent_config or {}).get("temperature", 0.7)
    system_prompt = (agent_config or {}).get("system_prompt", "")
    harness_pipeline = hc.get("preset") or (agent_config or {}).get("harness_pipeline", "standard")

    # ── RAG 선실행 (기존 로직) ──
    rag_context = await _pre_execute_rag_search(workflow_data, text, user_id)
    if rag_context:
        system_prompt = (system_prompt or "") + "\\n\\n" + rag_context
        logger.info(f"[HARNESS] RAG 선실행 결과 주입: {len(rag_context)}자")

    # ── 스테이지 리스트 (기존 로직) ──
    stages_list = None
    hc_stages = hc.get("stages")
    if isinstance(hc_stages, list) and hc_stages:
        stages_list = hc_stages
    elif isinstance(hc_stages, dict):
        stages_list = list(hc_stages.keys())

    # ── API 키 (기존 로직) ──
    _PROVIDER_KEY_MAP = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",
    }
    key_name = _PROVIDER_KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")
    api_key = os.environ.get(key_name, "")

    if not api_key:
        try:
            core_url = os.environ.get("XGEN_CORE_URL", "http://xgen-core:8000")
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
                for try_key in [key_name, "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
                    resp = await client.post(
                        f"{core_url}/api/data/config/get-value",
                        json={"env_name": try_key, "default": ""},
                    )
                    if resp.status_code == 200:
                        val = resp.json().get("value", "")
                        if val:
                            api_key = val
                            if try_key != key_name:
                                if "OPENAI" in try_key:
                                    provider = "openai"
                                    model = (agent_config or {}).get("openai_model", "gpt-4o-mini")
                                elif "ANTHROPIC" in try_key:
                                    provider = "anthropic"
                                    model = (agent_config or {}).get("anthropic_model", "claude-sonnet-4-6")
                            break
        except Exception:
            pass

    if not api_key:
        yield {"type": "error", "detail": "API 키가 설정되지 않았습니다."}
        return

    # ── 도구 URI 수집 (기존 로직) ──
    tools = ["mcp://bridge/nodes", "mcp://bridge/services"]
    mcp_sessions = _collect_mcp_session_ids(workflow_data)
    for sid in mcp_sessions:
        tools.append(f"mcp://session/{sid}")

    yield _make_log_event("INFO", f"[HARNESS] provider={provider}, model={model}, pipeline={harness_pipeline}")

    # ── Python Pipeline 실행 (NEW) ──
    try:
        from xgen_harness.integrations.workflow_bridge import execute_via_python_pipeline

        async for event in execute_via_python_pipeline(
            workflow_data=workflow_data,
            text=text,
            user_id=user_id,
            provider=provider,
            model=model,
            api_key=api_key,
            temperature=temperature,
            system_prompt=system_prompt,
            harness_pipeline=harness_pipeline,
            stages_list=stages_list,
            tools=tools,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            interaction_id=interaction_id,
            attached_files=attached_files,
            previous_results=previous_results,
            rag_context=rag_context,
            mcp_sessions=mcp_sessions,
        ):
            yield event
    except ImportError:
        yield {"type": "error", "detail": "xgen-harness 라이브러리 미설치"}
        yield {"type": "__harness_fallback__"}
    except Exception as e:
        logger.error(f"[HARNESS] Pipeline 실행 실패: {e}", exc_info=True)
        yield {"type": "error", "detail": f"하네스 실행 오류: {str(e)}"}
'''
