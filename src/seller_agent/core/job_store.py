from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Iterator

from seller_agent.core.job_models import (
    ApprovalRecord,
    ApprovalStatus,
    CardWorkItem,
    CardWorkStatus,
    JobEvent,
    JobRecord,
    JobStatus,
    ResourceLease,
    TelegramUpdateRecord,
)


DEFAULT_RUNTIME_DB = Path("runtime/runtime.db")


class JobStore:
    """Small SQLite-backed runtime state store for jobs, updates and leases."""

    def __init__(self, db_path: Path = DEFAULT_RUNTIME_DB) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA_SQL)

    def create_job(
        self,
        *,
        task_id: str,
        params: dict[str, Any] | None = None,
        actor: str = "system",
        job_id: str | None = None,
        status: JobStatus = "created",
        source: str = "",
    ) -> JobRecord:
        job_id = job_id or _new_job_id(task_id)
        now = _now()
        params_json = _json_dumps(params or {})
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                  job_id, task_id, status, actor, params_json, result_json,
                  error, created_at, updated_at, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, task_id, status, actor, params_json, "{}", "", now, now, "", ""),
            )
            connection.execute(
                """
                INSERT INTO task_requests (request_id, job_id, actor, source, params_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"{job_id}:request", job_id, actor, source, params_json, now),
            )
            self._insert_event(
                connection,
                job_id=job_id,
                event_type="job_created",
                message=f"Job created for task `{task_id}`.",
                data={"task_id": task_id, "status": status},
                created_at=now,
            )
        return self.get_job(job_id) or JobRecord(
            job_id=job_id,
            task_id=task_id,
            status=status,
            actor=actor,
            params=params or {},
            created_at=now,
            updated_at=now,
        )

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str = "",
        message: str = "",
    ) -> JobRecord:
        now = _now()
        started_at_update = ", started_at = COALESCE(NULLIF(started_at, ''), ?)" if status == "running" else ""
        finished_at_update = ", finished_at = ?" if status in TERMINAL_JOB_STATUSES else ""
        values: list[Any] = [status, _json_dumps(result or {}), error, now]
        if status == "running":
            values.append(now)
        if status in TERMINAL_JOB_STATUSES:
            values.append(now)
        values.append(job_id)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE jobs
                SET status = ?, result_json = ?, error = ?, updated_at = ?
                {started_at_update}
                {finished_at_update}
                WHERE job_id = ?
                """,
                tuple(values),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown job: {job_id}")
            self._insert_event(
                connection,
                job_id=job_id,
                event_type=f"job_{status}",
                message=message or f"Job status changed to `{status}`.",
                data={"status": status, "has_error": bool(error)},
                created_at=now,
            )
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job after update: {job_id}")
        return job

    def append_event(
        self,
        *,
        job_id: str,
        event_type: str,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> JobEvent:
        now = _now()
        with self._transaction() as connection:
            if not self._job_exists(connection, job_id):
                raise KeyError(f"Unknown job: {job_id}")
            event_id = self._insert_event(
                connection,
                job_id=job_id,
                event_type=event_type,
                message=message,
                data=data or {},
                created_at=now,
            )
        return JobEvent(
            event_id=event_id,
            job_id=job_id,
            event_type=event_type,
            message=message,
            data=data or {},
            created_at=now,
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _job_from_row(row) if row is not None else None

    def list_jobs(self, *, limit: int = 20, status: str | None = None, task_id: str | None = None) -> list[JobRecord]:
        self.initialize()
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("status = ?")
            values.append(status)
        if task_id:
            clauses.append("task_id = ?")
            values.append(task_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC, job_id DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def next_queued_job(self) -> JobRecord | None:
        """Return the oldest queued job so the worker processes the queue FIFO."""
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC, rowid ASC LIMIT 1"
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def list_events(self, job_id: str) -> list[JobEvent]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY event_id ASC",
                (job_id,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def register_telegram_update(
        self,
        *,
        update_id: int,
        chat_id: str,
        command: str = "",
        job_id: str = "",
        payload: dict[str, Any] | None = None,
        processing_status: str = "received",
    ) -> bool:
        now = _now()
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO telegram_updates (
                      update_id, chat_id, command, job_id, payload_json,
                      processing_status, received_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        update_id,
                        chat_id,
                        command,
                        job_id,
                        _json_dumps(payload or {}),
                        processing_status,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def update_telegram_update_status(
        self,
        *,
        update_id: int,
        processing_status: str,
        job_id: str = "",
    ) -> bool:
        now = _now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_updates
                SET processing_status = ?,
                    job_id = CASE WHEN ? != '' THEN ? ELSE job_id END,
                    updated_at = ?
                WHERE update_id = ?
                """,
                (processing_status, job_id, job_id, now, update_id),
            )
        return cursor.rowcount == 1

    def get_telegram_update(self, update_id: int) -> TelegramUpdateRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM telegram_updates WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        return _telegram_update_from_row(row) if row is not None else None

    def get_telegram_update_by_job_id(self, job_id: str) -> TelegramUpdateRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM telegram_updates
                WHERE job_id = ?
                ORDER BY received_at DESC, update_id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return _telegram_update_from_row(row) if row is not None else None

    def acquire_resource_lease(
        self,
        *,
        resource_key: str,
        owner_id: str,
        ttl_seconds: int,
        data: dict[str, Any] | None = None,
    ) -> ResourceLease | None:
        now_dt = datetime.now(timezone.utc)
        now = _format_dt(now_dt)
        expires_at = _format_dt(now_dt + timedelta(seconds=ttl_seconds))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM resource_leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
            if row is not None and str(row["expires_at"]) > now and row["owner_id"] != owner_id:
                return None
            connection.execute(
                """
                INSERT INTO resource_leases (resource_key, owner_id, acquired_at, expires_at, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(resource_key) DO UPDATE SET
                  owner_id = excluded.owner_id,
                  acquired_at = excluded.acquired_at,
                  expires_at = excluded.expires_at,
                  data_json = excluded.data_json
                """,
                (resource_key, owner_id, now, expires_at, _json_dumps(data or {})),
            )
        return ResourceLease(
            resource_key=resource_key,
            owner_id=owner_id,
            acquired_at=now,
            expires_at=expires_at,
            data=data or {},
        )

    def acquire_resource_leases(
        self,
        *,
        resource_keys: list[str] | tuple[str, ...],
        owner_id: str,
        ttl_seconds: int,
        data: dict[str, Any] | None = None,
    ) -> list[ResourceLease] | None:
        keys = [str(key).strip() for key in resource_keys if str(key).strip()]
        if not keys:
            return []
        if len(set(keys)) != len(keys):
            raise ValueError("resource_keys must be unique")

        now_dt = datetime.now(timezone.utc)
        now = _format_dt(now_dt)
        expires_at = _format_dt(now_dt + timedelta(seconds=ttl_seconds))
        leases: list[ResourceLease] = []
        with self._transaction() as connection:
            placeholders = ",".join("?" for _ in keys)
            rows = connection.execute(
                f"SELECT * FROM resource_leases WHERE resource_key IN ({placeholders})",
                tuple(keys),
            ).fetchall()
            for row in rows:
                if str(row["expires_at"]) > now and row["owner_id"] != owner_id:
                    return None

            data_json = _json_dumps(data or {})
            for key in keys:
                connection.execute(
                    """
                    INSERT INTO resource_leases (resource_key, owner_id, acquired_at, expires_at, data_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(resource_key) DO UPDATE SET
                      owner_id = excluded.owner_id,
                      acquired_at = excluded.acquired_at,
                      expires_at = excluded.expires_at,
                      data_json = excluded.data_json
                    """,
                    (key, owner_id, now, expires_at, data_json),
                )
                leases.append(
                    ResourceLease(
                        resource_key=key,
                        owner_id=owner_id,
                        acquired_at=now,
                        expires_at=expires_at,
                        data=data or {},
                    )
                )
        return leases

    def release_resource_lease(self, *, resource_key: str, owner_id: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM resource_leases WHERE resource_key = ? AND owner_id = ?",
                (resource_key, owner_id),
            )
        return cursor.rowcount == 1

    def release_resource_leases(self, *, resource_keys: list[str] | tuple[str, ...], owner_id: str) -> int:
        keys = [str(key).strip() for key in resource_keys if str(key).strip()]
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"DELETE FROM resource_leases WHERE owner_id = ? AND resource_key IN ({placeholders})",
                (owner_id, *keys),
            )
        return int(cursor.rowcount)

    def create_approval(
        self,
        *,
        approval_id: str,
        source_job_id: str,
        status: ApprovalStatus = "pending_review",
        checksum: str = "",
        data: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        now = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO approvals (
                  approval_id, source_job_id, status, owner_job_id, checksum,
                  data_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (approval_id, source_job_id, status, "", checksum, _json_dumps(data or {}), now, now),
            )
        record = self.get_approval(approval_id)
        if record is None:
            raise KeyError(f"Unknown approval after create: {approval_id}")
        return record

    def ensure_approval(
        self,
        *,
        approval_id: str,
        source_job_id: str,
        status: ApprovalStatus = "pending_review",
        checksum: str = "",
        data: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        now = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO approvals (
                  approval_id, source_job_id, status, owner_job_id, checksum,
                  data_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (approval_id, source_job_id, status, "", checksum, _json_dumps(data or {}), now, now),
            )
        record = self.get_approval(approval_id)
        if record is None:
            raise KeyError(f"Unknown approval after ensure: {approval_id}")
        return record

    def reserve_approval_for_apply(
        self,
        *,
        approval_id: str,
        owner_job_id: str,
        expected_checksum: str = "",
    ) -> bool:
        now = _now()
        checksum_clause = " AND checksum = ?" if expected_checksum else ""
        values: tuple[Any, ...] = (
            (owner_job_id, now, approval_id, expected_checksum)
            if expected_checksum
            else (owner_job_id, now, approval_id)
        )
        with self._transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE approvals
                SET status = 'applying', owner_job_id = ?, updated_at = ?
                WHERE approval_id = ? AND status = 'approved'
                {checksum_clause}
                """,
                values,
            )
        return cursor.rowcount == 1

    def update_approval_status(self, *, approval_id: str, status: ApprovalStatus) -> bool:
        now = _now()
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET status = ?, updated_at = ? WHERE approval_id = ?",
                (status, now, approval_id),
            )
        return cursor.rowcount == 1

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        return _approval_from_row(row) if row is not None else None

    def list_approvals(
        self,
        *,
        statuses: list[ApprovalStatus] | tuple[ApprovalStatus, ...] | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        self.initialize()
        clauses: list[str] = []
        values: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM approvals {where} ORDER BY updated_at ASC, approval_id ASC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [_approval_from_row(row) for row in rows]

    def upsert_card_work_item(
        self,
        *,
        internal_sku: str,
        status: CardWorkStatus,
        approval_id: str = "",
        plan_run_id: str = "",
        apply_run_id: str = "",
        post_verify_run_id: str = "",
        checksum: str = "",
        data: dict[str, Any] | None = None,
    ) -> CardWorkItem:
        now = _now()
        closed_at_expr = "excluded.closed_at" if status in {"closed", "failed"} else "card_work_items.closed_at"
        with self._transaction() as connection:
            connection.execute(
                f"""
                INSERT INTO card_work_items (
                  internal_sku, status, approval_id, plan_run_id, apply_run_id,
                  post_verify_run_id, checksum, data_json, created_at, updated_at,
                  closed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(internal_sku) DO UPDATE SET
                  status = excluded.status,
                  approval_id = CASE WHEN excluded.approval_id != '' THEN excluded.approval_id ELSE card_work_items.approval_id END,
                  plan_run_id = CASE WHEN excluded.plan_run_id != '' THEN excluded.plan_run_id ELSE card_work_items.plan_run_id END,
                  apply_run_id = CASE WHEN excluded.apply_run_id != '' THEN excluded.apply_run_id ELSE card_work_items.apply_run_id END,
                  post_verify_run_id = CASE WHEN excluded.post_verify_run_id != '' THEN excluded.post_verify_run_id ELSE card_work_items.post_verify_run_id END,
                  checksum = CASE WHEN excluded.checksum != '' THEN excluded.checksum ELSE card_work_items.checksum END,
                  data_json = excluded.data_json,
                  updated_at = excluded.updated_at,
                  closed_at = {closed_at_expr}
                """,
                (
                    internal_sku,
                    status,
                    approval_id,
                    plan_run_id,
                    apply_run_id,
                    post_verify_run_id,
                    checksum,
                    _json_dumps(data or {}),
                    now,
                    now,
                    now if status in {"closed", "failed"} else "",
                ),
            )
        item = self.get_card_work_item(internal_sku)
        if item is None:
            raise KeyError(f"Unknown card work item after upsert: {internal_sku}")
        return item

    def get_card_work_item(self, internal_sku: str) -> CardWorkItem | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM card_work_items WHERE internal_sku = ?",
                (internal_sku,),
            ).fetchone()
        return _card_work_item_from_row(row) if row is not None else None

    def list_card_work_items(
        self,
        *,
        status: CardWorkStatus | None = None,
        limit: int = 100,
    ) -> list[CardWorkItem]:
        self.initialize()
        where = "WHERE status = ?" if status else ""
        values: tuple[Any, ...] = (status, limit) if status else (limit,)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM card_work_items {where} ORDER BY updated_at DESC, internal_sku ASC LIMIT ?",
                values,
            ).fetchall()
        return [_card_work_item_from_row(row) for row in rows]

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any],
        created_at: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO job_events (job_id, event_type, message, data_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, event_type, message, _json_dumps(data), created_at),
        )
        return int(cursor.lastrowid)

    def _job_exists(self, connection: sqlite3.Connection, job_id: str) -> bool:
        row = connection.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return row is not None


TERMINAL_JOB_STATUSES = {"success", "partial_success", "failed", "timeout", "cancelled"}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  params_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS job_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  data_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id, event_id);

CREATE TABLE IF NOT EXISTS task_requests (
  request_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  actor TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  params_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  source_job_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  owner_job_id TEXT NOT NULL DEFAULT '',
  checksum TEXT NOT NULL DEFAULT '',
  data_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS resource_leases (
  resource_key TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS telegram_updates (
  update_id INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL DEFAULT '',
  command TEXT NOT NULL DEFAULT '',
  job_id TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  processing_status TEXT NOT NULL,
  received_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telegram_updates_status ON telegram_updates(processing_status);

CREATE TABLE IF NOT EXISTS card_work_items (
  internal_sku TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  approval_id TEXT NOT NULL DEFAULT '',
  plan_run_id TEXT NOT NULL DEFAULT '',
  apply_run_id TEXT NOT NULL DEFAULT '',
  post_verify_run_id TEXT NOT NULL DEFAULT '',
  checksum TEXT NOT NULL DEFAULT '',
  data_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_card_work_items_status ON card_work_items(status, updated_at);
"""


def _new_job_id(task_id: str) -> str:
    safe_task = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in task_id)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"job_{safe_task}_{timestamp}_{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return _format_dt(datetime.now(timezone.utc))


def _format_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str) -> dict[str, Any]:
    try:
        item = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return item if isinstance(item, dict) else {}


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=str(row["job_id"]),
        task_id=str(row["task_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        actor=str(row["actor"]),
        params=_json_loads(str(row["params_json"])),
        result=_json_loads(str(row["result_json"])),
        error=str(row["error"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=str(row["started_at"]),
        finished_at=str(row["finished_at"]),
    )


def _event_from_row(row: sqlite3.Row) -> JobEvent:
    return JobEvent(
        event_id=int(row["event_id"]),
        job_id=str(row["job_id"]),
        event_type=str(row["event_type"]),
        message=str(row["message"]),
        data=_json_loads(str(row["data_json"])),
        created_at=str(row["created_at"]),
    )


def _telegram_update_from_row(row: sqlite3.Row) -> TelegramUpdateRecord:
    return TelegramUpdateRecord(
        update_id=int(row["update_id"]),
        chat_id=str(row["chat_id"]),
        command=str(row["command"]),
        job_id=str(row["job_id"]),
        payload=_json_loads(str(row["payload_json"])),
        processing_status=str(row["processing_status"]),
        received_at=str(row["received_at"]),
    )


def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=str(row["approval_id"]),
        source_job_id=str(row["source_job_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        owner_job_id=str(row["owner_job_id"]),
        checksum=str(row["checksum"]),
        data=_json_loads(str(row["data_json"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _card_work_item_from_row(row: sqlite3.Row) -> CardWorkItem:
    return CardWorkItem(
        internal_sku=str(row["internal_sku"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        approval_id=str(row["approval_id"]),
        plan_run_id=str(row["plan_run_id"]),
        apply_run_id=str(row["apply_run_id"]),
        post_verify_run_id=str(row["post_verify_run_id"]),
        checksum=str(row["checksum"]),
        data=_json_loads(str(row["data_json"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        closed_at=str(row["closed_at"]),
    )
