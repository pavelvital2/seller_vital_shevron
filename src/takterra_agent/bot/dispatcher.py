from __future__ import annotations

from takterra_agent.tasks.registry import RegisteredTask, TaskRegistry


def build_registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(RegisteredTask(name="catalog_fetch", is_read_only=True, handler=None))
    return registry

