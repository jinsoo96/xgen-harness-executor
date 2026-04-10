use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use tracing::{debug, info, warn};

use super::protocol::*;

/// MCP 트랜스포트 타입
#[derive(Debug, Clone)]
pub enum McpTransport {
    /// 서브프로세스 stdin/stdout (xgen-mcp-station과 동일)
    Stdio {
        command: String,
        args: Vec<String>,
        env: HashMap<String, String>,
    },
    /// HTTP 기반 (xgen-mcp-station의 HTTP 엔드포인트 경유)
    Http {
        base_url: String,
        session_id: Option<String>,
    },
}

/// MCP 클라이언트 — 단일 MCP 서버와의 연결
pub struct McpClient {
    transport: McpTransport,
    request_id: AtomicU64,
    initialized: bool,
    /// 서버가 노출하는 도구 목록 (tools/list 결과 캐시)
    tools_cache: Vec<McpToolInfo>,
    /// stdio 트랜스포트 시 프로세스 핸들
    process: Option<StdioProcess>,
    /// HTTP 클라이언트
    http_client: reqwest::Client,
}

struct StdioProcess {
    child: Child,
    stdin: tokio::process::ChildStdin,
    stdout_reader: Arc<Mutex<BufReader<tokio::process::ChildStdout>>>,
}

/// MCP 서버가 노출하는 도구 정보
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpToolInfo {
    pub name: String,
    pub description: Option<String>,
    #[serde(rename = "inputSchema")]
    pub input_schema: serde_json::Value,
}

/// MCP 도구 호출 결과
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpToolResult {
    pub content: Vec<McpContent>,
    #[serde(rename = "isError", default)]
    pub is_error: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpContent {
    #[serde(rename = "type")]
    pub content_type: String,
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub data: Option<String>,
    #[serde(rename = "mimeType", default)]
    pub mime_type: Option<String>,
}

impl McpClient {
    pub fn new(transport: McpTransport) -> Self {
        Self {
            transport,
            request_id: AtomicU64::new(1),
            initialized: false,
            tools_cache: Vec::new(),
            process: None,
            http_client: reqwest::Client::new(),
        }
    }

    fn next_id(&self) -> u64 {
        self.request_id.fetch_add(1, Ordering::SeqCst)
    }

    /// 연결 + MCP 핸드셰이크
    pub async fn connect(&mut self) -> Result<()> {
        match &self.transport {
            McpTransport::Stdio { command, args, env } => {
                self.connect_stdio(command.clone(), args.clone(), env.clone())
                    .await?;
            }
            McpTransport::Http { base_url, session_id } => {
                // HTTP는 연결 불필요 — 세션이 없으면 생성 요청
                if session_id.is_none() {
                    info!(url = %base_url, "HTTP transport ready (no session)");
                }
            }
        }

        // MCP initialize 핸드셰이크
        self.initialize().await?;
        self.initialized = true;

        // 도구 목록 캐시
        self.tools_cache = self.list_tools_internal().await?;
        info!(
            tools = self.tools_cache.len(),
            "MCP client connected and initialized"
        );

        Ok(())
    }

    /// stdio 프로세스 시작
    async fn connect_stdio(
        &mut self,
        command: String,
        args: Vec<String>,
        env: HashMap<String, String>,
    ) -> Result<()> {
        info!(command = %command, args = ?args, "Starting MCP server process");

        let mut cmd = Command::new(&command);
        cmd.args(&args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .envs(&env);

        let mut child = cmd.spawn().context("Failed to spawn MCP server")?;

        let stdin = child.stdin.take().context("No stdin")?;
        let stdout = child.stdout.take().context("No stdout")?;

        // stderr 로깅 (백그라운드)
        if let Some(stderr) = child.stderr.take() {
            tokio::spawn(async move {
                let mut reader = BufReader::new(stderr);
                let mut line = String::new();
                loop {
                    line.clear();
                    match reader.read_line(&mut line).await {
                        Ok(0) => break,
                        Ok(_) => debug!(mcp_stderr = %line.trim(), "MCP server stderr"),
                        Err(e) => {
                            warn!(error = %e, "MCP stderr read error");
                            break;
                        }
                    }
                }
            });
        }

        self.process = Some(StdioProcess {
            child,
            stdin,
            stdout_reader: Arc::new(Mutex::new(BufReader::new(stdout))),
        });

        Ok(())
    }

    /// MCP initialize 핸드셰이크
    async fn initialize(&mut self) -> Result<()> {
        let request = JsonRpcRequest::initialize(self.next_id());
        let response = self.send_request(&request).await?;

        if let Some(error) = response.error {
            return Err(anyhow::anyhow!(
                "MCP initialize failed: {} (code: {})",
                error.message,
                error.code
            ));
        }

        // notifications/initialized 전송 (응답 불필요)
        let notification = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        });

        match &self.transport {
            McpTransport::Stdio { .. } => {
                if let Some(ref mut proc) = self.process {
                    let msg = serde_json::to_string(&notification)? + "\n";
                    proc.stdin.write_all(msg.as_bytes()).await?;
                    proc.stdin.flush().await?;
                }
            }
            McpTransport::Http { .. } => {
                // HTTP에서는 notification 무시 가능
            }
        }

        info!("MCP initialize handshake complete");
        Ok(())
    }

    /// JSON-RPC 요청 전송 + 응답 수신
    async fn send_request(&mut self, request: &JsonRpcRequest) -> Result<JsonRpcResponse> {
        match &self.transport {
            McpTransport::Stdio { .. } => self.send_stdio(request).await,
            McpTransport::Http { base_url, session_id } => {
                self.send_http(request, base_url.clone(), session_id.clone())
                    .await
            }
        }
    }

    /// stdio: stdin에 JSON-RPC 쓰기, stdout에서 응답 읽기
    async fn send_stdio(&mut self, request: &JsonRpcRequest) -> Result<JsonRpcResponse> {
        let proc = self
            .process
            .as_mut()
            .context("No stdio process running")?;

        let msg = serde_json::to_string(request)? + "\n";
        proc.stdin.write_all(msg.as_bytes()).await?;
        proc.stdin.flush().await?;

        // 응답 읽기 (idle timeout 30초, xgen-mcp-station과 동일)
        let reader = proc.stdout_reader.clone();
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(30),
            async {
                let mut reader = reader.lock().await;
                let mut line = String::new();
                loop {
                    line.clear();
                    let bytes = reader.read_line(&mut line).await?;
                    if bytes == 0 {
                        return Err(anyhow::anyhow!("MCP server stdout closed"));
                    }
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    // JSON-RPC 응답 파싱 시도
                    match serde_json::from_str::<JsonRpcResponse>(trimmed) {
                        Ok(resp) => return Ok(resp),
                        Err(_) => {
                            // notification이나 다른 메시지 — 무시하고 계속
                            debug!(line = trimmed, "Non-response line from MCP server");
                            continue;
                        }
                    }
                }
            },
        )
        .await
        .context("MCP request timed out (30s)")??;

        Ok(response)
    }

    /// HTTP: xgen-mcp-station 경유 JSON-RPC
    async fn send_http(
        &self,
        request: &JsonRpcRequest,
        base_url: String,
        session_id: Option<String>,
    ) -> Result<JsonRpcResponse> {
        // xgen-mcp-station의 /api/mcp/mcp-request 엔드포인트 사용
        let url = format!("{}/api/mcp/mcp-request", base_url);

        let body = serde_json::json!({
            "session_id": session_id,
            "method": request.method,
            "params": request.params,
        });

        let resp = self
            .http_client
            .post(&url)
            .json(&body)
            .timeout(std::time::Duration::from_secs(60))
            .send()
            .await
            .context("HTTP MCP request failed")?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow::anyhow!("MCP HTTP error {}: {}", status, text));
        }

        let mcp_resp: serde_json::Value = resp.json().await?;

        // xgen-mcp-station 응답을 JsonRpcResponse로 변환
        if mcp_resp["success"].as_bool() == Some(true) {
            Ok(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: Some(request.id),
                result: Some(mcp_resp["data"].clone()),
                error: None,
            })
        } else {
            Ok(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: Some(request.id),
                result: None,
                error: Some(JsonRpcError {
                    code: -1,
                    message: mcp_resp["error"]
                        .as_str()
                        .unwrap_or("Unknown error")
                        .to_string(),
                    data: None,
                }),
            })
        }
    }

    /// tools/list — 도구 목록 조회
    async fn list_tools_internal(&mut self) -> Result<Vec<McpToolInfo>> {
        let request = JsonRpcRequest::tools_list(self.next_id());
        let response = self.send_request(&request).await?;

        if let Some(error) = response.error {
            return Err(anyhow::anyhow!(
                "tools/list failed: {} (code: {})",
                error.message,
                error.code
            ));
        }

        let result = response.result.unwrap_or(serde_json::json!({"tools": []}));
        let tools: Vec<McpToolInfo> =
            serde_json::from_value(result["tools"].clone()).unwrap_or_default();

        Ok(tools)
    }

    /// 캐시된 도구 목록 반환
    pub fn tools(&self) -> &[McpToolInfo] {
        &self.tools_cache
    }

    /// tools/call — 도구 실행
    pub async fn call_tool(
        &mut self,
        name: &str,
        arguments: serde_json::Value,
    ) -> Result<McpToolResult> {
        if !self.initialized {
            return Err(anyhow::anyhow!("MCP client not initialized"));
        }

        debug!(tool = name, "Calling MCP tool");

        let request = JsonRpcRequest::tools_call(self.next_id(), name, arguments);
        let response = self.send_request(&request).await?;

        if let Some(error) = response.error {
            return Ok(McpToolResult {
                content: vec![McpContent {
                    content_type: "text".to_string(),
                    text: Some(format!("Error: {} (code: {})", error.message, error.code)),
                    data: None,
                    mime_type: None,
                }],
                is_error: true,
            });
        }

        let result = response
            .result
            .unwrap_or(serde_json::json!({"content": []}));

        let tool_result: McpToolResult = serde_json::from_value(result).unwrap_or(McpToolResult {
            content: vec![McpContent {
                content_type: "text".to_string(),
                text: Some("No result".to_string()),
                data: None,
                mime_type: None,
            }],
            is_error: false,
        });

        Ok(tool_result)
    }

    /// 연결 해제
    pub async fn disconnect(&mut self) -> Result<()> {
        if let Some(mut proc) = self.process.take() {
            // SIGTERM → 3초 대기 → SIGKILL (xgen-mcp-station과 동일)
            let _ = proc.child.kill().await;
            info!("MCP server process terminated");
        }
        self.initialized = false;
        self.tools_cache.clear();
        Ok(())
    }
}

impl Drop for McpClient {
    fn drop(&mut self) {
        if let Some(ref mut proc) = self.process {
            let _ = proc.child.start_kill();
        }
    }
}

/// 여러 MCP 서버를 관리하는 매니저
pub struct McpClientManager {
    clients: HashMap<String, McpClient>,
}

impl McpClientManager {
    pub fn new() -> Self {
        Self {
            clients: HashMap::new(),
        }
    }

    /// MCP 서버 추가 + 연결
    pub async fn add_server(
        &mut self,
        name: &str,
        transport: McpTransport,
    ) -> Result<()> {
        let mut client = McpClient::new(transport);
        client.connect().await?;
        self.clients.insert(name.to_string(), client);
        Ok(())
    }

    /// 전체 도구 목록 (네임스페이스 포함)
    pub fn all_tools(&self) -> Vec<(String, &McpToolInfo)> {
        let mut tools = Vec::new();
        for (server_name, client) in &self.clients {
            for tool in client.tools() {
                tools.push((server_name.clone(), tool));
            }
        }
        tools
    }

    /// 도구 이름으로 서버 + 클라이언트 찾기
    pub fn find_tool(&self, tool_name: &str) -> Option<(&str, &McpToolInfo)> {
        for (server_name, client) in &self.clients {
            if let Some(tool) = client.tools().iter().find(|t| t.name == tool_name) {
                return Some((server_name, tool));
            }
        }
        None
    }

    /// 도구 호출
    pub async fn call_tool(
        &mut self,
        tool_name: &str,
        arguments: serde_json::Value,
    ) -> Result<McpToolResult> {
        // 도구가 속한 서버 찾기
        let server_name = {
            let mut found = None;
            for (name, client) in &self.clients {
                if client.tools().iter().any(|t| t.name == tool_name) {
                    found = Some(name.clone());
                    break;
                }
            }
            found.context(format!("Tool '{}' not found in any MCP server", tool_name))?
        };

        let client = self
            .clients
            .get_mut(&server_name)
            .context("Server not found")?;

        client.call_tool(tool_name, arguments).await
    }

    /// 모든 서버 연결 해제
    pub async fn disconnect_all(&mut self) -> Result<()> {
        for (name, client) in self.clients.iter_mut() {
            if let Err(e) = client.disconnect().await {
                warn!(server = %name, error = %e, "Failed to disconnect MCP server");
            }
        }
        self.clients.clear();
        Ok(())
    }
}
