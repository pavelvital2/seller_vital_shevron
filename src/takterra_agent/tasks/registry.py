from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RegisteredTask:
    name: str
    is_read_only: bool
    handler: Callable


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, RegisteredTask] = {}

    def register(self, task: RegisteredTask) -> None:
        self._tasks[task.name] = task

    def get(self, name: str) -> RegisteredTask:
        return self._tasks[name]

    def names(self) -> list[str]:
        return sorted(self._tasks)

