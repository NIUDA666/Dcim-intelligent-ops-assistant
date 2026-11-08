"""独立任务并行执行与部分失败隔离（§10.4）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ValidatedTask:
    task_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolGateway(Protocol):
    async def invoke(self, *, context: Any, task: ValidatedTask) -> dict[str, Any]: ...


async def execute_independent(
    tasks: list[ValidatedTask],
    *,
    gateway: ToolGateway,
    access_context: Any,
    timeout_seconds: float = 8.0,
    max_parallel: int = 4,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max_parallel)

    async def run_one(task: ValidatedTask) -> dict[str, Any]:
        async with semaphore:
            try:
                async with asyncio.timeout(timeout_seconds):
                    result = await gateway.invoke(context=access_context, task=task)
                return {"task_id": task.task_id, "status": "ok", "result": result}
            except TimeoutError:
                return {
                    "task_id": task.task_id,
                    "status": "error",
                    "error_code": "UPSTREAM_TIMEOUT",
                }
            except asyncio.CancelledError:
                raise
            except Exception:
                # 详细异常只进服务端日志；前端拿受控错误码，不拿堆栈。
                return {
                    "task_id": task.task_id,
                    "status": "error",
                    "error_code": "TOOL_EXECUTION_FAILED",
                }

    return list(await asyncio.gather(*(run_one(task) for task in tasks)))
