//! 통합 테스트 — 실행기 전체 흐름 검증


// ── 상태 머신 전이 테스트 ──────────────────────────────────────

#[cfg(test)]
mod state_machine_tests {
    

    #[test]
    fn test_stage_presets() {
        use xgen_harness_executor::state_machine::stage::HarnessStage;

        // minimal: input, system_prompt, llm, complete (4단계)
        let minimal = HarnessStage::preset("minimal");
        assert_eq!(minimal.len(), 4);
        assert_eq!(minimal[0], HarnessStage::Bootstrap);
        assert_eq!(minimal[1], HarnessStage::ContextBuild);
        assert_eq!(minimal[2], HarnessStage::LLMCall);
        assert_eq!(minimal[3], HarnessStage::Complete);

        // standard: 7단계
        let standard = HarnessStage::preset("standard");
        assert_eq!(standard.len(), 7);
        assert!(standard.contains(&HarnessStage::Plan));
        assert!(standard.contains(&HarnessStage::ToolDiscovery));

        // anthropic: 11단계
        let anthropic = HarnessStage::preset("anthropic");
        assert_eq!(anthropic.len(), 11);
        assert!(anthropic.contains(&HarnessStage::Plan));
        assert!(anthropic.contains(&HarnessStage::Validate));
        assert!(anthropic.contains(&HarnessStage::Decide));

        // full: 12단계
        let full = HarnessStage::preset("full");
        assert_eq!(full.len(), 12);
        assert!(full.contains(&HarnessStage::MemoryWrite));

        // unknown → minimal 폴백
        let unknown = HarnessStage::preset("nonexistent");
        assert_eq!(unknown.len(), 4);
    }

    #[test]
    fn test_stage_display_names() {
        use xgen_harness_executor::state_machine::stage::HarnessStage;

        // 12단계 신규 이름
        assert_eq!(HarnessStage::Bootstrap.display_name(), "Input");
        assert_eq!(HarnessStage::MemoryRead.display_name(), "Memory");
        assert_eq!(HarnessStage::ContextBuild.display_name(), "System Prompt");
        assert_eq!(HarnessStage::Plan.display_name(), "Plan");
        assert_eq!(HarnessStage::ToolDiscovery.display_name(), "Tool Index");
        assert_eq!(HarnessStage::ContextCompact.display_name(), "Context");
        assert_eq!(HarnessStage::LLMCall.display_name(), "LLM");
        assert_eq!(HarnessStage::ToolExecute.display_name(), "Execute");
        assert_eq!(HarnessStage::Validate.display_name(), "Validate");
        assert_eq!(HarnessStage::Decide.display_name(), "Decide");
        assert_eq!(HarnessStage::MemoryWrite.display_name(), "Save");
        assert_eq!(HarnessStage::Complete.display_name(), "Complete");
        // 레거시 compat
        assert_eq!(HarnessStage::Init.display_name(), "Init");
        assert_eq!(HarnessStage::Execute.display_name(), "Execute(legacy)");
        assert_eq!(HarnessStage::Error.display_name(), "Error");
    }

    #[test]
    fn test_stage_transition_types() {
        use xgen_harness_executor::state_machine::stage::{HarnessStage, StageTransition};

        // Next 전이
        let next = StageTransition::Next;
        matches!(next, StageTransition::Next);

        // JumpTo 전이 (재시도)
        let jump = StageTransition::JumpTo(HarnessStage::Plan);
        match jump {
            StageTransition::JumpTo(target) => assert_eq!(target, HarnessStage::Plan),
            _ => panic!("Expected JumpTo"),
        }

        // Complete 전이
        let complete = StageTransition::Complete(serde_json::json!({"text": "done"}));
        match complete {
            StageTransition::Complete(val) => assert_eq!(val["text"], "done"),
            _ => panic!("Expected Complete"),
        }

        // Error 전이
        let error = StageTransition::Error("test error".to_string());
        match error {
            StageTransition::Error(msg) => assert_eq!(msg, "test error"),
            _ => panic!("Expected Error"),
        }
    }
}

// ── SSE 이벤트 테스트 ──────────────────────────────────────────

#[cfg(test)]
mod sse_tests {
    #[test]
    fn test_sse_event_format() {
        use xgen_harness_executor::events::SseEvent;

        let event = SseEvent::text("hello world");
        let formatted = event.to_sse_string();
        assert!(formatted.contains("event: message\n"));
        assert!(formatted.contains("hello world"));
        assert!(formatted.ends_with("\n\n"));
    }

    #[test]
    fn test_sse_error_event() {
        use xgen_harness_executor::events::SseEvent;

        let event = SseEvent::error("something broke");
        assert_eq!(event.event, "error");
        assert_eq!(event.data["message"], "something broke");
    }

    #[test]
    fn test_sse_done_event() {
        use xgen_harness_executor::events::SseEvent;

        let result = serde_json::json!({"text": "final answer", "iterations": 3});
        let event = SseEvent::done(result.clone());
        assert_eq!(event.event, "done");
        assert_eq!(event.data["text"], "final answer");
        assert_eq!(event.data["iterations"], 3);
    }

    #[test]
    fn test_sse_event_with_id() {
        use xgen_harness_executor::events::SseEvent;

        let event = SseEvent {
            event: "stage_enter".to_string(),
            data: serde_json::json!({"stage": "Execute"}),
            id: Some("evt-123".to_string()),
        };
        let formatted = event.to_sse_string();
        assert!(formatted.contains("id: evt-123\n"));
        assert!(formatted.contains("event: stage_enter\n"));
    }
}

// ── LLM Provider 구조 테스트 ───────────────────────────────────

#[cfg(test)]
mod llm_tests {
    #[test]
    fn test_provider_factory() {
        use xgen_harness_executor::llm::provider::create_provider;

        let anthropic = create_provider("anthropic", "test-key", None);
        assert!(anthropic.is_ok());
        assert_eq!(anthropic.unwrap().name(), "anthropic");

        let openai = create_provider("openai", "test-key", None);
        assert!(openai.is_ok());
        assert_eq!(openai.unwrap().name(), "openai");

        let unknown = create_provider("unknown_provider", "key", None);
        assert!(unknown.is_err());
    }

    #[test]
    fn test_chat_request_serialization() {
        use xgen_harness_executor::llm::provider::{ChatRequest, ChatMessage, MessageContent};

        let request = ChatRequest {
            model: "claude-sonnet-4-6".to_string(),
            messages: vec![ChatMessage {
                role: "user".to_string(),
                content: MessageContent::Text("hello".to_string()),
                tool_calls: None,
                tool_call_id: None,
            }],
            system: Some("You are helpful.".to_string()),
            temperature: 0.7,
            max_tokens: 1024,
            tools: None,
        };

        let json = serde_json::to_value(&request).unwrap();
        assert_eq!(json["model"], "claude-sonnet-4-6");
        assert_eq!(json["temperature"], 0.7);
        assert_eq!(json["max_tokens"], 1024);
    }

    #[test]
    fn test_tool_definition() {
        use xgen_harness_executor::llm::provider::ToolDefinition;

        let tool = ToolDefinition {
            name: "search_docs".to_string(),
            description: "Search documents".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }),
        };

        let json = serde_json::to_value(&tool).unwrap();
        assert_eq!(json["name"], "search_docs");
        assert!(json["input_schema"]["properties"]["query"].is_object());
    }
}

// ── 컨텍스트 관리 통합 테스트 ──────────────────────────────────

#[cfg(test)]
mod context_tests {
    #[test]
    fn test_context_manager_full_flow() {
        use xgen_harness_executor::context::window::ContextWindowManager;

        let mut mgr = ContextWindowManager::new("anthropic");

        // 초기 상태 확인
        let status = mgr.get_status();
        assert_eq!(status["provider"], "anthropic");
        assert_eq!(status["max_tokens"], 200_000);
        assert_eq!(status["needs_compaction"], false);

        // 존 트래킹
        mgr.track("system_prompt", "You are helpful.");
        mgr.track("rag_context", &"document content ".repeat(100));

        let status = mgr.get_status();
        assert!(status["current_tokens"].as_u64().unwrap() > 0);

        // 압축은 아직 불필요
        let mut history = vec![serde_json::json!({"role": "user", "content": "hi"})];
        let mut rag = "short context".to_string();
        let compacted = mgr.check_and_compact("system", &mut history, &mut rag);
        assert!(!compacted); // 예산 내
    }

    #[test]
    fn test_context_manager_compaction_flow() {
        use xgen_harness_executor::context::window::ContextWindowManager;

        let mut mgr = ContextWindowManager::new("vllm"); // 32K — 작은 윈도우

        // 큰 히스토리로 예산 초과 유도
        let mut history: Vec<serde_json::Value> = (0..20)
            .map(|i| serde_json::json!({
                "role": if i % 2 == 0 { "user" } else { "assistant" },
                "content": format!("Message {} with lots of content {}", i, "x".repeat(500)),
            }))
            .collect();

        let mut rag = "x".repeat(10000);

        // 수동으로 사용량 설정 (threshold 초과)
        mgr.budget.current_usage_chars = 40_000; // 32K 윈도우에서 초과

        let compacted = mgr.check_and_compact("system", &mut history, &mut rag);
        assert!(compacted);
        assert_eq!(history.len(), 4); // 최근 4개만 유지
        assert!(rag.len() < 10000); // RAG도 축소됨
    }

    #[test]
    fn test_prompt_section_priority_ordering() {
        use xgen_harness_executor::context::sections::PromptSectionManager;

        let mut mgr = PromptSectionManager::new();
        mgr.add_role("Role definition.");           // priority 1
        mgr.add("rag", Some("RAG data ".repeat(1000)), false, 5, true); // priority 5
        mgr.add("chat", Some("Chat context ".repeat(500)), false, 7, true); // priority 7

        // 작은 예산 → 높은 priority(chat=7)부터 제거
        let result = mgr.build(Some(2000));
        assert!(result.contains("Role definition")); // priority 1은 절대 제거 안 됨
    }

    #[test]
    fn test_memory_prefetch_full_flow() {
        use xgen_harness_executor::context::memory::MemoryPrefetcher;

        let mut pf = MemoryPrefetcher::new();

        let previous = vec![
            "React 컴포넌트 설계에 대한 답변".to_string(),
            "Python 데이터 처리에 대한 답변".to_string(),
            "React hooks 활용법".to_string(),
            "SQL 최적화 방법".to_string(),
        ];

        let feedback = vec![
            "간결한 답변 선호".to_string(),
            "코드 예시 포함 요청".to_string(),
        ];

        let result = pf.prefetch("React 컴포넌트 만들기", &previous, &feedback);
        assert!(!result.is_empty());
        assert!(result.contains("이전 관련 답변"));
        assert!(result.contains("사용자 선호"));
    }
}

// ── 에러 복구 통합 테스트 ──────────────────────────────────────

#[cfg(test)]
mod recovery_tests {
    #[test]
    fn test_recovery_full_sequence() {
        use xgen_harness_executor::stages::recover::ErrorRecoveryManager;

        let mut mgr = ErrorRecoveryManager::new();

        // 1. 첫 413 → compact
        let action = mgr.attempt_recovery("context_length_exceeded 413", "claude-sonnet-4-6");
        assert!(matches!(action, xgen_harness_executor::stages::recover::RecoveryAction::Compact { .. }));

        // 2. 두번째 413 → give_up (이미 compact 시도)
        let action = mgr.attempt_recovery("413 again", "claude-sonnet-4-6");
        assert!(matches!(action, xgen_harness_executor::stages::recover::RecoveryAction::GiveUp { .. }));
    }

    #[test]
    fn test_max_tokens_escalation_sequence() {
        use xgen_harness_executor::stages::recover::{ErrorRecoveryManager, RecoveryAction, ESCALATED_MAX_TOKENS};

        let mut mgr = ErrorRecoveryManager::new();

        // 1. 첫 max_tokens → escalate
        let action = mgr.attempt_recovery("max_tokens exceeded", "gpt-4o");
        match action {
            RecoveryAction::Escalate { new_max_tokens } => {
                assert_eq!(new_max_tokens, ESCALATED_MAX_TOKENS);
            }
            _ => panic!("Expected Escalate"),
        }

        // 2. 두번째 → retry (recovery count 1/3)
        let action = mgr.attempt_recovery("max_tokens exceeded", "gpt-4o");
        assert!(matches!(action, RecoveryAction::Retry { .. }));

        // 3. 세번째 → retry (2/3)
        let action = mgr.attempt_recovery("max_tokens exceeded", "gpt-4o");
        assert!(matches!(action, RecoveryAction::Retry { .. }));

        // 4. 네번째 → retry (3/3)
        let action = mgr.attempt_recovery("max_tokens exceeded", "gpt-4o");
        assert!(matches!(action, RecoveryAction::Retry { .. }));

        // 5. 다섯번째 → give_up (exhausted)
        let action = mgr.attempt_recovery("max_tokens exceeded", "gpt-4o");
        assert!(matches!(action, RecoveryAction::GiveUp { .. }));
    }

    #[test]
    fn test_success_resets_state() {
        use xgen_harness_executor::stages::recover::ErrorRecoveryManager;

        let mut mgr = ErrorRecoveryManager::new();
        mgr.state.consecutive_failures = 2;
        mgr.state.max_output_tokens_recovery_count = 2;

        mgr.state.reset_on_success();
        assert_eq!(mgr.state.consecutive_failures, 0);
        assert_eq!(mgr.state.max_output_tokens_recovery_count, 0);
    }
}

// ── 워크플로우 정의 통합 테스트 ────────────────────────────────

#[cfg(test)]
mod workflow_tests {
    #[test]
    fn test_full_workflow_lifecycle() {
        use xgen_harness_executor::workflow::definition::HarnessWorkflow;
        use xgen_harness_executor::state_machine::stage::HarnessStage;

        // 1. JSON 파싱
        let json = r#"{
            "version": "harness-v1",
            "orchestration": "pipeline",
            "agents": [
                {
                    "id": "generator",
                    "name": "Code Generator",
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "stages": ["init", "plan", "execute", "complete"],
                    "tools": ["mcp://sandbox/python"],
                    "config": {
                        "system_prompt": "You are a code generator.",
                        "temperature": 0.3,
                        "max_tokens": 16384,
                        "modules": ["context_manager", "error_recovery"]
                    }
                },
                {
                    "id": "evaluator",
                    "name": "Code Evaluator",
                    "stages": "anthropic",
                    "config": {
                        "eval_threshold": 0.8,
                        "max_retries": 5
                    }
                }
            ],
            "connections": [
                {"from": "generator", "to": "evaluator", "artifact": "code_output"}
            ]
        }"#;

        let workflow: HarnessWorkflow = serde_json::from_str(json).unwrap();

        // 2. 유효성 검증
        assert!(workflow.validate().is_ok());

        // 3. AgentDefinition 변환
        let defs = workflow.to_agent_definitions();
        assert_eq!(defs.len(), 2);

        // generator 검증
        assert_eq!(defs[0].config.provider_name, "anthropic");
        assert_eq!(defs[0].config.temperature, 0.3);
        assert_eq!(defs[0].config.max_tokens, 16384);
        assert_eq!(defs[0].tools, vec!["mcp://sandbox/python"]);
        assert!(defs[0].config.modules.contains(&"context_manager".to_string()));

        // evaluator 검증 (프리셋 "anthropic")
        assert!(defs[1].stages.contains(&HarnessStage::Validate));
        assert!(defs[1].stages.contains(&HarnessStage::Decide));
        assert_eq!(defs[1].config.eval_threshold, 0.8);
        assert_eq!(defs[1].config.max_retries, 5);
    }

    #[test]
    fn test_legacy_conversion_roundtrip() {
        use xgen_harness_executor::workflow::converter::{LegacyWorkflow, LegacyNode, LegacyEdge, LegacyEdgeEnd, convert_legacy_to_harness};

        let legacy = LegacyWorkflow {
            nodes: vec![
                LegacyNode {
                    id: "n1".to_string(),
                    name: "Agent A".to_string(),
                    node_id: "agents/xgen".to_string(),
                    parameters: serde_json::json!({
                        "provider": "openai",
                        "model": "gpt-4.1",
                        "harness_pipeline": "anthropic",
                        "temperature": 0.5,
                        "system_prompt": "You are Agent A."
                    }),
                },
                LegacyNode {
                    id: "n2".to_string(),
                    name: "Agent B".to_string(),
                    node_id: "agents/xgen".to_string(),
                    parameters: serde_json::json!({
                        "harness_pipeline": "claude_code"
                    }),
                },
            ],
            edges: vec![LegacyEdge {
                source: LegacyEdgeEnd { node_id: "n1".to_string(), port_id: "stream".to_string() },
                target: LegacyEdgeEnd { node_id: "n2".to_string(), port_id: "text".to_string() },
            }],
        };

        let workflow = convert_legacy_to_harness(&legacy, "wf-test", "Test Workflow").unwrap();

        // 변환 검증
        assert_eq!(workflow.version, "harness-v1");
        assert_eq!(workflow.agents.len(), 2);
        assert_eq!(workflow.connections.len(), 1);

        // 에이전트 A 검증
        assert_eq!(workflow.agents[0].provider, "openai");
        assert_eq!(workflow.agents[0].model, "gpt-4.1");
        assert_eq!(workflow.agents[0].config.system_prompt, Some("You are Agent A.".to_string()));
        assert_eq!(workflow.agents[0].config.temperature, Some(0.5));

        // 유효성 검증
        assert!(workflow.validate().is_ok());

        // harness-v1 JSON 직렬화 가능 확인
        let json = serde_json::to_string_pretty(&workflow).unwrap();
        assert!(json.contains("harness-v1"));

        // 재파싱 가능 확인
        let reparsed: xgen_harness_executor::workflow::definition::HarnessWorkflow =
            serde_json::from_str(&json).unwrap();
        assert_eq!(reparsed.agents.len(), 2);
    }
}

// ── MCP 프로토콜 테스트 ────────────────────────────────────────

#[cfg(test)]
mod mcp_tests {
    #[test]
    fn test_jsonrpc_request_serialization() {
        use xgen_harness_executor::mcp::protocol::JsonRpcRequest;

        let req = JsonRpcRequest::initialize(1);
        let json = serde_json::to_value(&req).unwrap();
        assert_eq!(json["jsonrpc"], "2.0");
        assert_eq!(json["id"], 1);
        assert_eq!(json["method"], "initialize");
        assert!(json["params"]["protocolVersion"].as_str().unwrap().contains("2024"));
    }

    #[test]
    fn test_tools_list_request() {
        use xgen_harness_executor::mcp::protocol::JsonRpcRequest;

        let req = JsonRpcRequest::tools_list(42);
        let json = serde_json::to_value(&req).unwrap();
        assert_eq!(json["method"], "tools/list");
        assert_eq!(json["id"], 42);
    }

    #[test]
    fn test_tools_call_request() {
        use xgen_harness_executor::mcp::protocol::JsonRpcRequest;

        let req = JsonRpcRequest::tools_call(
            99,
            "search_documents",
            serde_json::json!({"query": "React hooks"}),
        );
        let json = serde_json::to_value(&req).unwrap();
        assert_eq!(json["method"], "tools/call");
        assert_eq!(json["params"]["name"], "search_documents");
        assert_eq!(json["params"]["arguments"]["query"], "React hooks");
    }
}

// ── 도구 레지스트리 테스트 ─────────────────────────────────────

#[cfg(test)]
mod tool_registry_tests {
    #[test]
    fn test_tool_registry_and_roles() {
        use xgen_harness_executor::tools::registry::{ToolRegistry, AgentRole};
        use xgen_harness_executor::mcp::client::McpToolInfo;

        let mut registry = ToolRegistry::new();

        let tools = vec![
            McpToolInfo {
                name: "search_documents".to_string(),
                description: Some("Search docs".to_string()),
                input_schema: serde_json::json!({"type": "object"}),
            },
            McpToolInfo {
                name: "execute_python".to_string(),
                description: Some("Run Python code".to_string()),
                input_schema: serde_json::json!({"type": "object"}),
            },
            McpToolInfo {
                name: "read_file".to_string(),
                description: Some("Read a file".to_string()),
                input_schema: serde_json::json!({"type": "object"}),
            },
        ];

        registry.register_server_tools("test-server", tools);
        assert_eq!(registry.total_tools(), 3);

        // Explore = 읽기 전용만
        let explore_tools = registry.get_tools_for_role(&AgentRole::Explore);
        assert_eq!(explore_tools.len(), 2); // search + read
        assert!(explore_tools.iter().all(|t| t.name != "execute_python"));

        // Generator = 전부
        let gen_tools = registry.get_tools_for_role(&AgentRole::Generator);
        assert_eq!(gen_tools.len(), 3);

        // Custom = 지정만
        let custom_tools = registry.get_tools_for_role(&AgentRole::Custom(vec!["read_file".to_string()]));
        assert_eq!(custom_tools.len(), 1);
        assert_eq!(custom_tools[0].name, "read_file");
    }

    #[test]
    fn test_tool_index_progressive_disclosure() {
        use xgen_harness_executor::tools::registry::ToolRegistry;
        use xgen_harness_executor::mcp::client::McpToolInfo;

        let mut registry = ToolRegistry::new();
        let tools = vec![
            McpToolInfo { name: "search_docs".to_string(), description: None, input_schema: serde_json::json!({}) },
            McpToolInfo { name: "execute_python".to_string(), description: None, input_schema: serde_json::json!({}) },
            McpToolInfo { name: "web_api_call".to_string(), description: None, input_schema: serde_json::json!({}) },
        ];
        registry.register_server_tools("srv", tools);

        // 카테고리 인덱스 (스키마 없이 이름만)
        let index = registry.get_tool_index();
        assert!(!index.is_empty());

        // 카테고리별 도구 (스키마 포함)
        for cat in &index {
            let cat_tools = registry.get_tools_by_category(&cat.name);
            assert!(!cat_tools.is_empty());
        }
    }
}
