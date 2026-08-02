from __future__ import annotations

from pathlib import Path

from seller_agent.bot.job_notifier import notify_telegram_job_result
from seller_agent.bot.telegram_runner import TelegramRunnerError
from seller_agent.core.job_service import JobService
from seller_agent.core.job_store import JobStore
from seller_agent.tasks.registry import RegisteredTask, TaskRegistry


def _registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(
        RegisteredTask(
            name="store-analytics-overview",
            command="store-analytics-overview",
            title="Overview",
            description="Read-only overview.",
            mode="read_only",
            risk="low",
            parameter_schema={
                "marketplace": {"type": "string", "enum": ["ozon", "wb"], "required": True},
                "period_days": {"type": "integer", "enum": [7, 30, 90], "required": True},
            },
        )
    )
    return registry


def test_control_job_uses_server_route_and_new_token_once(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, registry=_registry(), data_dir=tmp_path / "data")
    submission = service.submit_control_read_only(
        task_id="store-analytics-overview",
        params={"marketplace": "wb", "period_days": 30},
        owner_id="42",
        chat_id="42",
        idempotency_key="notify-control-1",
        allowed_task_ids={"store-analytics-overview"},
    )
    store.update_job_status(
        submission.job.job_id,
        "success",
        result={"status": "ok", "summary": {"overall_status": "ok"}},
    )
    calls: list[tuple[str, str, dict]] = []

    def fake_api(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        return {"ok": True, "result": {"message_id": 901}}

    first = notify_telegram_job_result(
        token="old-default-token",
        control_token_loader=lambda: "new-control-token",
        job_id=submission.job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=fake_api,
    )
    second = notify_telegram_job_result(
        token="old-default-token",
        control_token_loader=lambda: "new-control-token",
        job_id=submission.job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=fake_api,
    )

    assert first.ok is True
    assert first.chat_id == 42
    assert calls == [("new-control-token", "sendMessage", calls[0][2])]
    assert submission.public_job_id in calls[0][2]["text"]
    assert submission.job.job_id not in calls[0][2]["text"]
    assert second.ok is True
    assert second.blocked_reason == "already_notified"
    assert store.get_job_notification_route(submission.job.job_id)["processing_status"] == "sent"  # type: ignore[index]
    assert store.get_telegram_update_by_job_id(submission.job.job_id) is None


def test_control_route_never_falls_back_to_old_token(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, registry=_registry(), data_dir=tmp_path / "data")
    submission = service.submit_control_read_only(
        task_id="store-analytics-overview",
        params={"marketplace": "ozon", "period_days": 7},
        owner_id="42",
        chat_id="42",
        idempotency_key="notify-control-2",
        allowed_task_ids={"store-analytics-overview"},
    )
    store.update_job_status(
        submission.job.job_id,
        "success",
        result={"summary": {"overall_status": "ok"}},
    )
    calls: list[str] = []

    result = notify_telegram_job_result(
        token="old-default-token",
        control_token_loader=lambda: "",
        job_id=submission.job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=lambda token, method, payload: calls.append(token) or {"ok": True},
    )

    assert result.ok is False
    assert result.blocked_reason == "control_token_unavailable"
    assert calls == []
    assert store.get_job_notification_route(submission.job.job_id)["processing_status"] == "failed"  # type: ignore[index]
    available = store.get_control_job_for_owner(
        owner_id="42",
        public_job_id=submission.public_job_id,
    )
    assert available is not None and available.status == "success"


def test_partial_control_notification_is_failed_not_exactly_once(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    report = data_dir / "reports" / "overview.md"
    report.parent.mkdir(parents=True)
    report.write_text("safe test report", encoding="utf-8")
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, registry=_registry(), data_dir=data_dir)
    submission = service.submit_control_read_only(
        task_id="store-analytics-overview",
        params={"marketplace": "ozon", "period_days": 30},
        owner_id="42",
        chat_id="42",
        idempotency_key="notify-control-partial",
        allowed_task_ids={"store-analytics-overview"},
    )
    store.update_job_status(
        submission.job.job_id,
        "success",
        result={
            "summary": {"overall_status": "ok"},
            "artifacts": {"report": str(report)},
        },
    )
    text_calls: list[str] = []
    document_calls: list[str] = []

    def fail_document(token: str, method: str, payload: dict, path: Path) -> dict:
        document_calls.append(token)
        raise TelegramRunnerError("test document delivery failure")

    result = notify_telegram_job_result(
        token="old-default-token",
        control_token_loader=lambda: "new-control-token",
        job_id=submission.job.job_id,
        store=store,
        data_dir=data_dir,
        api_request=lambda token, method, payload: text_calls.append(token) or {"ok": True},
        document_api_request=fail_document,
    )

    assert result.ok is False
    assert text_calls == ["new-control-token"]
    assert document_calls == ["new-control-token"]
    assert store.get_job_notification_route(submission.job.job_id)["processing_status"] == "failed"  # type: ignore[index]
    assert store.get_control_job_for_owner(
        owner_id="42", public_job_id=submission.public_job_id
    ) is not None


def test_control_notification_contract_does_not_claim_retry_or_exactly_once() -> None:
    runbook = Path("data/planning/stage3_control_plane_runbook.md").read_text(
        encoding="utf-8"
    )

    assert "best-effort" in runbook
    assert "автоматического retry notification нет" in runbook
    assert "не заявляется как\nexactly-once" in runbook
    assert "Fallback на token старого bot запрещён" in runbook


def test_control_telegram_owner_copy_is_russian_and_hides_dynamic_status(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "runtime.db")
    service = JobService(store=store, registry=_registry(), data_dir=tmp_path / "data")
    submission = service.submit_control_read_only(
        task_id="store-analytics-overview",
        params={"marketplace": "ozon", "period_days": 30},
        owner_id="42",
        chat_id="42",
        idempotency_key="notify-control-owner-copy",
        allowed_task_ids={"store-analytics-overview"},
    )
    store.update_job_status(
        submission.job.job_id,
        "success",
        result={
            "summary": {
                "overall_status": "secret_dynamic_status",
                "marketplace": "secret_dynamic_marketplace",
                "period_days": 30,
            }
        },
    )
    payloads: list[dict] = []

    result = notify_telegram_job_result(
        token="old-default-token",
        control_token_loader=lambda: "new-control-token",
        job_id=submission.job.job_id,
        store=store,
        data_dir=tmp_path / "data",
        api_request=lambda token, method, payload: payloads.append(payload) or {"ok": True},
    )

    assert result.ok is True
    text = payloads[0]["text"]
    for forbidden in (
        "Marketplace",
        "Marketplace write",
        "Mini App",
        "secret_dynamic_status",
        "secret_dynamic_marketplace",
    ):
        assert forbidden not in text
    assert "Статус: `неизвестно`" in text
    assert "Изменения на площадке не выполнялись." in text
