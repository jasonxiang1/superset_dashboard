# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Transactional, workspace-scoped persistence for operational projects and tasks."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import cast, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, func, select, Table, text

from superset.operations.tables import OperationsTables
from superset.utils import json

Resource = Literal[
    "projects",
    "tasks",
    "notes",
    "labor_entries",
    "assignments",
    "workers",
    "attachments",
]
Record = dict[str, object]


class OperationsError(Exception):
    """An expected operational failure safe to return to the client."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def serialize(record: Record) -> Record:
    """Encode UUIDs and timestamps without exposing SQLAlchemy row objects."""
    return {
        key: value.astimezone(timezone.utc).isoformat()
        if isinstance(value, datetime)
        else str(value)
        if isinstance(value, (UUID, Decimal, date))
        else value
        for key, value in record.items()
    }


class OperationsRepository:
    """Operate within one external DB transaction and one authorized workspace."""

    def __init__(
        self,
        connection: Connection,
        tables: OperationsTables,
        workspace_id: UUID,
        actor_id: str,
        *,
        business_timezone: str = "UTC",
        currency: str = "USD",
        financials_enabled: bool = False,
    ) -> None:
        if connection.dialect.name != "postgresql":
            raise OperationsError(503, "Operations requires a PostgreSQL connection.")
        self.connection = connection
        self.tables = tables
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.business_timezone = business_timezone
        self.currency = currency
        self.financials_enabled = financials_enabled

    def _table(self, resource: Resource) -> Table:
        return {
            "projects": self.tables.projects,
            "tasks": self.tables.tasks,
            "notes": self.tables.notes,
            "labor_entries": self.tables.labor_entries,
            "assignments": self.tables.assignments,
            "workers": self.tables.workers,
            "attachments": self.tables.attachments,
        }[resource]

    def _load(
        self, resource: Resource, record_id: UUID, *, lock: bool = False
    ) -> Record:
        table = self._table(resource)
        statement = select(table).where(
            table.c.workspace_id == self.workspace_id, table.c.id == record_id
        )
        if lock:
            statement = statement.with_for_update()
        row = self.connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise OperationsError(404, "Record not found.")
        return dict(row)

    def get(self, resource: Resource, record_id: UUID) -> Record:
        """Read a scoped record, including its recoverable archived state."""
        return serialize(self._load(resource, record_id))

    def list_records(
        self,
        resource: Resource,
        *,
        archived: bool,
        limit: int,
        offset: int,
        project_id: UUID | None = None,
    ) -> list[Record]:
        """List bounded current-state records without exposing audit history."""
        table = self._table(resource)
        statement = select(table).where(table.c.workspace_id == self.workspace_id)
        if project_id is not None:
            if resource in {"projects", "workers"}:
                raise OperationsError(400, "This resource has no project filter.")
            self._load("projects", project_id)
            statement = statement.where(table.c.project_id == project_id)
        if not archived:
            statement = statement.where(table.c.archived_at.is_(None))
            if resource not in {"projects", "workers"}:
                project = self.tables.projects
                statement = statement.join(
                    project,
                    (project.c.id == table.c.project_id)
                    & (project.c.workspace_id == table.c.workspace_id),
                ).where(project.c.archived_at.is_(None))
        statement = (
            statement.order_by(table.c.updated_at.desc(), table.c.id)
            .limit(limit)
            .offset(offset)
        )
        return [
            serialize(dict(row))
            for row in self.connection.execute(statement).mappings()
        ]

    def _labor_values(
        self, attributes: Record, *, resolve_rate: bool, require_assignment: bool = True
    ) -> Record:
        """Validate worker assignment and snapshot the rate effective on work date."""
        worker_id = cast(UUID, attributes["worker_id"])
        worker = self._load("workers", worker_id, lock=True)
        if require_assignment and worker["archived_at"] is not None:
            raise OperationsError(409, "Worker is inactive.")
        assignments = self.tables.assignments
        assignment = self.connection.execute(
            select(assignments.c.id).where(
                assignments.c.workspace_id == self.workspace_id,
                assignments.c.project_id == attributes["project_id"],
                assignments.c.worker_id == worker_id,
                assignments.c.archived_at.is_(None),
            )
        ).first()
        if require_assignment and assignment is None:
            raise OperationsError(
                409, "Assign the worker to this project before logging hours."
            )
        rate = cast(Decimal, attributes.get("hourly_rate", Decimal(0)))
        if resolve_rate:
            rates = self.tables.rates
            effective = (
                self.connection.execute(
                    select(rates)
                    .where(
                        rates.c.workspace_id == self.workspace_id,
                        rates.c.worker_id == worker_id,
                        rates.c.effective_from <= attributes["work_date"],
                    )
                    .order_by(rates.c.effective_from.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if effective is None:
                raise OperationsError(409, "No labor rate exists for this work date.")
            if effective["currency"] != self.currency:
                raise OperationsError(
                    409, "Worker rate currency does not match the workspace."
                )
            rate = effective["hourly_rate"]
        cost = (rate * Decimal(cast(int, attributes["minutes"])) / 60).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return {
            "hourly_rate": rate,
            "cost": cost,
            "currency": self.currency
            if resolve_rate
            else attributes.get("currency", self.currency),
        }

    def _assignment_worker(self, attributes: Record) -> None:
        worker = self._load("workers", cast(UUID, attributes["worker_id"]), lock=True)
        if worker["archived_at"] is not None:
            raise OperationsError(409, "Worker is inactive.")

    def _audit(
        self, resource: Resource, action: str, before: Record | None, after: Record
    ) -> None:
        self.connection.execute(
            self.tables.audit.insert().values(
                id=uuid4(),
                workspace_id=self.workspace_id,
                resource=resource,
                record_id=after["id"],
                actor_id=self.actor_id,
                action=action,
                occurred_at=datetime.now(timezone.utc),
                before=serialize(before) if before else None,
                after=serialize(after),
            )
        )

    def _active_project(self, project_id: UUID) -> Record:
        project = self._load("projects", project_id, lock=True)
        if project["archived_at"] is not None:
            raise OperationsError(409, "Restore the project before changing its tasks.")
        return project

    def _touch_project(self, project_id: UUID, occurred_at: datetime) -> None:
        table = self.tables.projects
        self.connection.execute(
            table.update()
            .where(table.c.workspace_id == self.workspace_id, table.c.id == project_id)
            .values(updated_at=occurred_at, revision=table.c.revision + 1)
        )

    def create(self, resource: Resource, payload: Record) -> Record:
        """Create once per request UUID, atomically recording its audit and replay."""
        request_id = cast(UUID, payload["request_id"])
        attributes = {
            key: value for key, value in payload.items() if key != "request_id"
        }
        fingerprint = sha256(
            json.dumps([resource, serialize(attributes)], sort_keys=True).encode()
        ).hexdigest()
        # Serialize requests before checking replay; a retry can arrive before commit.
        lock_key = int.from_bytes(
            sha256(f"{self.workspace_id}:{request_id}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        self.connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
        )
        requests = self.tables.requests
        previous = (
            self.connection.execute(
                select(requests).where(
                    requests.c.workspace_id == self.workspace_id,
                    requests.c.request_id == request_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if previous:
            if (
                previous["actor_id"] != self.actor_id
                or previous["fingerprint"] != fingerprint
            ):
                raise OperationsError(
                    409, "Request ID already used for another submission."
                )
            return cast(Record, previous["result"])
        occurred_at = datetime.now(timezone.utc)
        if resource not in {"projects", "workers"}:
            project_id = cast(UUID, attributes["project_id"])
            self._active_project(project_id)
            if resource == "tasks":
                attributes.update(completed=False, completed_at=None)
            elif resource == "labor_entries":
                attributes.update(self._labor_values(attributes, resolve_rate=True))
            elif resource == "assignments":
                self._assignment_worker(attributes)
            self._touch_project(project_id, occurred_at)
        record = {
            **attributes,
            "id": uuid4(),
            "workspace_id": self.workspace_id,
            "revision": 1,
            "created_at": occurred_at,
            "updated_at": occurred_at,
            "archived_at": None,
        }
        self.connection.execute(self._table(resource).insert().values(**record))
        self._audit(resource, "create", None, record)
        result = serialize(record)
        self.connection.execute(
            requests.insert().values(
                workspace_id=self.workspace_id,
                request_id=request_id,
                actor_id=self.actor_id,
                fingerprint=fingerprint,
                result=result,
            )
        )
        return result

    def change(
        self,
        resource: Resource,
        record_id: UUID,
        payload: Record,
        action: Literal["edit", "archive", "restore", "complete"],
    ) -> Record:
        """Apply a revision-checked mutation with audit in the same transaction."""
        if resource not in {"projects", "workers"}:
            # Lock parent before child in every mutation to avoid archive/write races.
            task = self._load(resource, record_id)
            self._active_project(cast(UUID, task["project_id"]))
        before = self._load(resource, record_id, lock=True)
        if before["revision"] != payload["revision"]:
            raise OperationsError(409, "Record changed. Reload before saving.")
        archived = before["archived_at"] is not None
        if archived != (action == "restore"):
            raise OperationsError(409, "Record is not in the required archive state.")
        occurred_at = datetime.now(timezone.utc)
        changes = {key: value for key, value in payload.items() if key != "revision"}
        if action in {"archive", "restore"}:
            changes = {"archived_at": occurred_at if action == "archive" else None}
        if action == "complete":
            changes["completed_at"] = occurred_at if changes["completed"] else None
        if resource == "labor_entries" and action in {"edit", "restore"}:
            merged = {**before, **changes}
            if action == "edit" and set(changes).intersection(
                {"worker_id", "work_date", "minutes"}
            ):
                changes.update(
                    self._labor_values(
                        merged,
                        require_assignment=merged["worker_id"] != before["worker_id"],
                        resolve_rate=any(
                            merged[key] != before[key]
                            for key in ("worker_id", "work_date")
                        ),
                    )
                )
        if resource == "assignments" and action == "restore":
            self._assignment_worker(before)
        changes.update(
            revision=cast(int, before["revision"]) + 1, updated_at=occurred_at
        )
        table = self._table(resource)
        self.connection.execute(
            table.update()
            .where(table.c.workspace_id == self.workspace_id, table.c.id == record_id)
            .values(**changes)
        )
        after = {**before, **changes}
        if resource not in {"projects", "workers"}:
            self._touch_project(cast(UUID, before["project_id"]), occurred_at)
        self._audit(resource, action, before, after)
        return serialize(after)

    def history(
        self, resource: Resource, record_id: UUID, offset: int = 0
    ) -> list[Record]:
        """Read scoped audit snapshots; the API requires editorship for this method."""
        self._load(resource, record_id)
        audit = self.tables.audit
        return [
            serialize(dict(row))
            for row in self.connection.execute(
                select(audit)
                .where(
                    audit.c.workspace_id == self.workspace_id,
                    audit.c.resource == resource,
                    audit.c.record_id == record_id,
                )
                .order_by(audit.c.occurred_at.desc(), audit.c.id)
                .limit(50)
                .offset(offset)
            ).mappings()
        ]

    def project_financials(self) -> dict[str, Record]:
        """Combine financial snapshots with all unvoided historical labor costs."""
        if not self.financials_enabled:
            return {}
        financials, labor = self.tables.financials, self.tables.labor_entries
        if self.connection.execute(
            select(labor.c.id)
            .where(
                labor.c.workspace_id == self.workspace_id,
                labor.c.archived_at.is_(None),
                labor.c.currency != self.currency,
            )
            .limit(1)
        ).first():
            raise OperationsError(
                503, "Labor currency does not match the financial source."
            )
        costs = {
            row.project_id: row.cost
            for row in self.connection.execute(
                select(labor.c.project_id, func.sum(labor.c.cost).label("cost"))
                .where(
                    labor.c.workspace_id == self.workspace_id,
                    labor.c.archived_at.is_(None),
                )
                .group_by(labor.c.project_id)
            )
        }
        result: dict[str, Record] = {}
        for row in self.connection.execute(
            select(financials).where(financials.c.workspace_id == self.workspace_id)
        ).mappings():
            if row["currency"] != self.currency:
                raise OperationsError(
                    503, "Financial source currency does not match the workspace."
                )
            cost = costs.get(row["project_id"], Decimal(0))
            result[str(row["project_id"])] = {
                **dict(row),
                "labor_cost": cost,
                "labor_due": max(cost - row["labor_paid"], Decimal(0)),
            }
        return result

    def summary(self, day: date | None = None) -> Record:
        """Calculate today's tasks, open jobs, and optional gross financial metrics."""
        zone = ZoneInfo(self.business_timezone)
        day = day or datetime.now(zone).date()
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(
            timezone.utc
        )
        tasks, projects = self.tables.tasks, self.tables.projects
        condition = (
            (tasks.c.workspace_id == self.workspace_id)
            & tasks.c.archived_at.is_(None)
            & (tasks.c.due_at >= start)
            & (tasks.c.due_at < end)
            & projects.c.archived_at.is_(None)
        )
        today_tasks = [
            serialize(dict(row))
            for row in self.connection.execute(
                select(tasks)
                .join(
                    projects,
                    (projects.c.id == tasks.c.project_id)
                    & (projects.c.workspace_id == tasks.c.workspace_id),
                )
                .where(condition)
                .order_by(tasks.c.due_at, tasks.c.id)
                .limit(100)
            ).mappings()
        ]
        total = self.connection.scalar(
            select(func.count())
            .select_from(tasks)
            .join(
                projects,
                (projects.c.id == tasks.c.project_id)
                & (projects.c.workspace_id == tasks.c.workspace_id),
            )
            .where(condition)
        )
        completed = self.connection.scalar(
            select(func.count())
            .select_from(tasks)
            .join(
                projects,
                (projects.c.id == tasks.c.project_id)
                & (projects.c.workspace_id == tasks.c.workspace_id),
            )
            .where(condition, tasks.c.completed.is_(True))
        )
        open_jobs = self.connection.scalar(
            select(func.count())
            .select_from(projects)
            .where(
                projects.c.workspace_id == self.workspace_id,
                projects.c.archived_at.is_(None),
                projects.c.status.notin_(["completed", "cancelled"]),
            )
        )
        financials = self.project_financials()
        # Incomplete snapshots must not produce deceptively partial workspace totals.
        project_count = self.connection.scalar(
            select(func.count())
            .select_from(projects)
            .where(projects.c.workspace_id == self.workspace_id)
        )
        available = (
            self.financials_enabled
            and len(financials) == project_count
            and project_count != 0
        )
        revenue = sum(
            (cast(Decimal, row["revenue"]) for row in financials.values()), Decimal(0)
        )
        cost = sum(
            (
                cast(Decimal, row["non_labor_cost"]) + cast(Decimal, row["labor_cost"])
                for row in financials.values()
            ),
            Decimal(0),
        )
        labor_due = sum(
            (cast(Decimal, row["labor_due"]) for row in financials.values()), Decimal(0)
        )
        return {
            "day": day.isoformat(),
            "timezone": self.business_timezone,
            "currency": self.currency,
            "items_today": total,
            "completed_today": completed,
            "open_jobs": open_jobs,
            "tasks": today_tasks,
            "tasks_has_more": cast(int, total) > 100,
            "labor_due": str(labor_due) if available else None,
            "margin_percent": str(
                ((revenue - cost) / revenue * 100).quantize(Decimal("0.01"))
            )
            if available and revenue > 0
            else None,
            "financial_period": "all_recorded_work",
            "project_financials": {
                key: serialize(value) for key, value in financials.items()
            },
        }
