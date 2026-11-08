"""Return anticipated tool failures with useful messages, without exposing tracebacks."""

from collections.abc import Awaitable, Callable
from functools import wraps

from mcp.server.mcpserver.exceptions import ToolError

from joinery.mcp_runtime import PolicyStop


def tool_errors[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await function(*args, **kwargs)
        except PolicyStop as exc:
            raise ToolError("Rule Zero STOP: " + str(exc)) from exc
        except (ValueError, RuntimeError, OSError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapped
