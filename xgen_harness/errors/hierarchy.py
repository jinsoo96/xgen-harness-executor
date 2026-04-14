"""
xgen-harness 에러 계층

모든 하네스 에러의 기반. 스테이지별 에러, 프로바이더 에러, 도구 에러 등을 포함.
ErrorCategory로 재시도 가능 여부를 판단.
"""

from enum import Enum
from typing import Optional


class ErrorCategory(Enum):
    RATE_LIMIT = "rate_limit"
    OVERLOAD = "overload"
    TIMEOUT = "timeout"
    NETWORK = "network"
    TOKEN_LIMIT = "token_limit"
    AUTH = "auth"
    BAD_REQUEST = "bad_request"
    SERVER = "server"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"

    @property
    def recoverable(self) -> bool:
        return self in {
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.OVERLOAD,
            ErrorCategory.TIMEOUT,
            ErrorCategory.NETWORK,
            ErrorCategory.SERVER,
        }


class HarnessError(Exception):
    """모든 하네스 에러의 기반 클래스"""

    def __init__(
        self,
        message: str,
        stage_id: str = "",
        category: ErrorCategory = ErrorCategory.UNKNOWN,
    ):
        self.stage_id = stage_id
        self.category = category
        super().__init__(message)

    @property
    def recoverable(self) -> bool:
        return self.category.recoverable


class ConfigError(HarnessError):
    """API 키 누락, 잘못된 모델, 잘못된 설정"""

    def __init__(self, message: str, stage_id: str = ""):
        super().__init__(message, stage_id, ErrorCategory.AUTH)


class ProviderError(HarnessError):
    """LLM API 에러 기반"""

    def __init__(
        self,
        message: str,
        stage_id: str = "s07_llm",
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        http_status: Optional[int] = None,
    ):
        self.http_status = http_status
        super().__init__(message, stage_id, category)

    @classmethod
    def from_status(cls, status: int, body: str = "") -> "ProviderError":
        if status == 429:
            return RateLimitError(f"Rate limit: {body}")
        elif status == 529:
            return OverloadError(f"Overloaded: {body}")
        elif status == 401:
            return cls(f"Auth failed: {body}", category=ErrorCategory.AUTH, http_status=status)
        elif status >= 500:
            return cls(f"Server error {status}: {body}", category=ErrorCategory.SERVER, http_status=status)
        else:
            return cls(f"HTTP {status}: {body}", category=ErrorCategory.BAD_REQUEST, http_status=status)


class RateLimitError(ProviderError):
    def __init__(self, message: str = "Rate limited"):
        super().__init__(message, category=ErrorCategory.RATE_LIMIT, http_status=429)


class OverloadError(ProviderError):
    def __init__(self, message: str = "API overloaded"):
        super().__init__(message, category=ErrorCategory.OVERLOAD, http_status=529)


class ContextOverflowError(ProviderError):
    def __init__(self, message: str = "Context window exceeded"):
        super().__init__(message, category=ErrorCategory.TOKEN_LIMIT)


class ToolError(HarnessError):
    """도구 실행 실패"""

    def __init__(self, message: str, tool_name: str = "", stage_id: str = "s08_execute"):
        self.tool_name = tool_name
        super().__init__(message, stage_id, ErrorCategory.UNKNOWN)


class ToolTimeoutError(ToolError):
    def __init__(self, tool_name: str, timeout_seconds: float):
        super().__init__(f"Tool '{tool_name}' timed out after {timeout_seconds}s", tool_name)


class MCPConnectionError(ToolError):
    def __init__(self, message: str, session_id: str = ""):
        self.session_id = session_id
        super().__init__(message, tool_name=f"mcp:{session_id}")


class ValidationError(HarnessError):
    """Validate 스테이지에서 품질 미달 감지"""

    def __init__(self, message: str, score: float = 0.0):
        self.score = score
        super().__init__(message, "s09_validate", ErrorCategory.UNKNOWN)


class PipelineAbortError(HarnessError):
    """복구 불가 실패, 파이프라인 중단"""

    def __init__(self, message: str, stage_id: str = ""):
        super().__init__(message, stage_id, ErrorCategory.TERMINAL)
