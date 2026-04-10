use std::sync::Arc;

use futures_util::future::join_all;
use tokio::sync::Mutex;
use tracing::{debug, info};

use crate::llm::provider::ToolCall;
use crate::mcp::client::{McpClientManager, McpToolResult};

/// 도구 결과 크기 제한 (Python tool_orchestration.py에서 포팅)
const TOOL_RESULT_BUDGET_CHARS: usize = 50_000;
/// 디스크 저장 임계값 (이 이상이면 /tmp에 저장 + 2KB 프리뷰)
const DISK_SAVE_THRESHOLD_CHARS: usize = 50_000;
/// Microcompact: 앞부분 + 뒷부분만 남기기
const MICROCOMPACT_HEAD_CHARS: usize = 800;
const MICROCOMPACT_TAIL_CHARS: usize = 500;
/// 디스크 저장 디렉토리
const TOOL_RESULT_DIR: &str = "/tmp/harness_tool_results";

/// 도구 호출 오케스트레이션
/// Read=병렬, Write=직렬 (Python tool_orchestration.py 포팅)
pub struct ToolOrchestrator;

impl ToolOrchestrator {
    /// 도구 호출 목록을 병렬/직렬로 파티셔닝하여 실행
    pub async fn execute_tool_calls(
        mcp_manager: &Arc<Mutex<McpClientManager>>,
        tool_calls: &[ToolCall],
    ) -> Vec<ToolCallResult> {
        let (read_calls, write_calls) = partition_tool_calls(tool_calls);

        let mut results = Vec::new();

        // 1. 읽기 도구: 진정한 병렬 실행 (tokio::spawn + join_all)
        if !read_calls.is_empty() {
            debug!(count = read_calls.len(), "Executing read tools in parallel");
            let parallel_results =
                execute_parallel(mcp_manager, &read_calls).await;
            results.extend(parallel_results);
        }

        // 2. 쓰기 도구: 직렬 실행
        for tc in &write_calls {
            debug!(tool = %tc.name, "Executing write tool sequentially");
            let mut mgr = mcp_manager.lock().await;
            let result = execute_single(&mut mgr, tc).await;
            results.push(result);
        }

        // 3. 결과 크기 제한 적용 (50K+ → 디스크 저장 + 프리뷰 반환)
        for result in &mut results {
            if result.content.len() > DISK_SAVE_THRESHOLD_CHARS {
                result.content = save_large_result_to_disk(&result.tool_name, &result.content).await;
            }
        }

        results
    }
}

/// 도구 호출 결과
pub struct ToolCallResult {
    pub tool_call_id: String,
    pub tool_name: String,
    pub content: String,
    pub is_error: bool,
}

/// Read/Write 파티셔닝
/// (Python tool_orchestration.py: partition_tool_calls 포팅)
fn partition_tool_calls(calls: &[ToolCall]) -> (Vec<&ToolCall>, Vec<&ToolCall>) {
    let mut reads = Vec::new();
    let mut writes = Vec::new();

    for call in calls {
        if is_read_tool(&call.name) {
            reads.push(call);
        } else {
            writes.push(call);
        }
    }

    (reads, writes)
}

fn is_read_tool(name: &str) -> bool {
    let lower = name.to_lowercase();
    let read_patterns = [
        "search", "read", "list", "get", "query", "fetch", "find",
        "check", "view", "browse", "inspect",
    ];
    read_patterns.iter().any(|p| lower.contains(p))
}

/// 병렬 실행 (tokio::spawn + join_all)
async fn execute_parallel(
    mcp_manager: &Arc<Mutex<McpClientManager>>,
    calls: &[&ToolCall],
) -> Vec<ToolCallResult> {
    let handles: Vec<_> = calls
        .iter()
        .map(|call| {
            let mgr = Arc::clone(mcp_manager);
            let call = (*call).clone();
            tokio::spawn(async move {
                let mut mgr = mgr.lock().await;
                execute_single(&mut mgr, &call).await
            })
        })
        .collect();

    let join_results = join_all(handles).await;

    join_results
        .into_iter()
        .map(|r| match r {
            Ok(result) => result,
            Err(e) => ToolCallResult {
                tool_call_id: String::new(),
                tool_name: "unknown".to_string(),
                content: format!("Task join error: {}", e),
                is_error: true,
            },
        })
        .collect()
}

/// 단일 도구 실행
async fn execute_single(
    mcp_manager: &mut McpClientManager,
    tool_call: &ToolCall,
) -> ToolCallResult {
    match mcp_manager
        .call_tool(&tool_call.name, tool_call.input.clone())
        .await
    {
        Ok(mcp_result) => {
            let content = mcp_result_to_string(&mcp_result);
            ToolCallResult {
                tool_call_id: tool_call.id.clone(),
                tool_name: tool_call.name.clone(),
                content,
                is_error: mcp_result.is_error,
            }
        }
        Err(e) => ToolCallResult {
            tool_call_id: tool_call.id.clone(),
            tool_name: tool_call.name.clone(),
            content: format!("Error calling tool '{}': {}", tool_call.name, e),
            is_error: true,
        },
    }
}

/// MCP 결과를 문자열로 변환
fn mcp_result_to_string(result: &McpToolResult) -> String {
    result
        .content
        .iter()
        .filter_map(|c| c.text.as_deref())
        .collect::<Vec<_>>()
        .join("\n")
}

/// 대용량 도구 결과를 /tmp에 저장하고 2KB 프리뷰를 반환
/// (Claude Code tool_result_budget.py 포팅: disk_save + preview)
async fn save_large_result_to_disk(tool_name: &str, content: &str) -> String {
    // 디렉토리 보장 (실패해도 fallback)
    if let Err(e) = tokio::fs::create_dir_all(TOOL_RESULT_DIR).await {
        // 디렉토리 생성 실패 → microcompact로 폴백
        tracing::warn!(error = %e, "Failed to create tool result dir, using microcompact");
        return microcompact_text(content);
    }

    // 파일명: tool_name + timestamp + .txt
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let filename = format!("{}/{}_{}.txt", TOOL_RESULT_DIR, tool_name.replace('/', "_"), ts);

    match tokio::fs::write(&filename, content.as_bytes()).await {
        Ok(()) => {
            info!(
                tool = tool_name,
                size = content.len(),
                path = %filename,
                "Tool result saved to disk"
            );
            // 2KB 프리뷰 + 파일 경로 참조 반환
            let preview_chars = 2048;
            let preview = if content.len() > preview_chars {
                &content[..preview_chars]
            } else {
                content
            };
            format!(
                "[대용량 결과 디스크 저장: {} bytes → {}]\n\n[미리보기 — 처음 2KB]\n{}\n\n... (전체 결과는 {} 에서 확인)",
                content.len(),
                filename,
                preview,
                filename
            )
        }
        Err(e) => {
            tracing::warn!(error = %e, tool = tool_name, "Disk save failed, using microcompact");
            microcompact_text(content)
        }
    }
}

/// Microcompact — 큰 결과를 앞뒤만 남기고 축소
/// (Python tool_orchestration.py: microcompact_tool_result 포팅)
fn microcompact_text(text: &str) -> String {
    if text.len() <= MICROCOMPACT_HEAD_CHARS + MICROCOMPACT_TAIL_CHARS + 50 {
        return text.to_string();
    }

    let head = &text[..MICROCOMPACT_HEAD_CHARS];
    let tail = &text[text.len() - MICROCOMPACT_TAIL_CHARS..];
    let omitted = text.len() - MICROCOMPACT_HEAD_CHARS - MICROCOMPACT_TAIL_CHARS;

    format!(
        "{}\n\n... ({} characters omitted) ...\n\n{}",
        head, omitted, tail
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_microcompact() {
        let short = "hello world";
        assert_eq!(microcompact_text(short), short);

        let long = "a".repeat(60_000);
        let result = microcompact_text(&long);
        assert!(result.len() < 2000);
        assert!(result.contains("characters omitted"));
    }

    #[test]
    fn test_partition() {
        let calls = vec![
            ToolCall {
                id: "1".into(),
                name: "search_documents".into(),
                input: serde_json::json!({}),
            },
            ToolCall {
                id: "2".into(),
                name: "execute_python".into(),
                input: serde_json::json!({}),
            },
            ToolCall {
                id: "3".into(),
                name: "read_file".into(),
                input: serde_json::json!({}),
            },
        ];

        let (reads, writes) = partition_tool_calls(&calls);
        assert_eq!(reads.len(), 2); // search_documents, read_file
        assert_eq!(writes.len(), 1); // execute_python
    }
}
