from __future__ import annotations

import json

import pytest

from takterra_agent.bot.dispatcher import build_registry as build_bot_registry, telegram_tasks
from takterra_agent.cli import build_parser, main
from takterra_agent.tasks.registry import default_task_registry, get_task_definition, list_task_definitions


def test_task_registry_covers_cli_commands() -> None:
    parser_commands = _parser_commands()
    registry_commands = set(default_task_registry().commands())

    assert parser_commands <= registry_commands


def test_task_registry_contains_safety_metadata_for_write_tasks() -> None:
    registry = default_task_registry()
    write_tasks = [task for task in registry.list() if task.mode == "apply"]

    assert write_tasks
    assert all(task.requires_confirmation for task in write_tasks)
    assert all(task.risk in {"low", "high"} for task in write_tasks)


def test_task_registry_filters_and_alias_lookup() -> None:
    ozon_apply = list_task_definitions(mode="apply", marketplace="ozon")
    task = get_task_definition("apply-ozon-cpc-bids")

    assert any(row["command"] == "apply-ozon-cpc-bids" for row in ozon_apply)
    assert task["name"] == "ozon-cpc-bids-apply"
    assert task["requires_confirmation"] is True


def test_bot_dispatcher_uses_default_task_registry() -> None:
    bot_registry = build_bot_registry()
    telegram_commands = {task["telegram_button_label"] for task in telegram_tasks()}

    assert bot_registry.commands() == default_task_registry().commands()
    assert {"/status", "/today", "/reviews", "/approvals"} <= telegram_commands


def test_cli_tasks_list_and_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tasks", "list", "--mode", "apply", "--marketplace", "wb"]) == 0
    list_output = json.loads(capsys.readouterr().out)
    assert list_output["rows"]
    assert all(row["mode"] == "apply" for row in list_output["rows"])
    assert all("wb" in row["marketplaces"] for row in list_output["rows"])

    assert main(["tasks", "show", "--task", "reviews-questions"]) == 0
    show_output = json.loads(capsys.readouterr().out)
    assert show_output["task"]["command"] == "reviews-questions"
    assert show_output["task"]["runbook_path"] == "data/planning/reviews_questions_runbook.md"


def _parser_commands() -> set[str]:
    parser = build_parser()
    for action in parser._actions:  # noqa: SLF001 - argparse exposes subcommands only through parser actions.
        if action.dest == "command":
            return set(action.choices)
    raise AssertionError("CLI command action not found")
