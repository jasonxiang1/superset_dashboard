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
from decimal import Decimal
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
    "summaries",
]
Record = dict[str, object]
FINANCIAL_FIELDS = {
    "hourly_rate",
    "cost",
    "currency",
    "revenue",
    "non_labor_cost",
    "client_receivable",
    "labor_paid",
    "labor_cost",
    "labor_due",
    "margin_percent",
    "financial_period",
    "project_financials",
}
TOP_LEVEL_RESOURCES = {"projects", "workers", "summaries"}


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
        else serialize(value)
        if isinstance(value, dict)
        else value
        for key, value in record.items()
        if key not in FINANCIAL_FIELDS
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
    ) -> None:
        if connection.dialect.name != "postgresql":
            raise OperationsError(503, "Operations requires a PostgreSQL connection.")
        self.connection = connection
        self.tables = tables
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.business_timezone = business_timezone

    def _table(self, resource: Resource) -> Table:
        return {
            "projects": self.tables.projects,
            "tasks": self.tables.tasks,
            "notes": self.tables.notes,
            "labor_entries": self.tables.labor_entries,
            "assignments": self.tables.assignments,
            "workers": self.tables.workers,
            "attachments": self.tables.attachments,
            "summaries": self.tables.summaries,
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
        summary_date: date | None = None,
        summary_id: UUID | None = None,
    ) -> list[Record]:
        """List bounded current-state records without exposing audit history."""
        table = self._table(resource)
        statement = select(table).where(table.c.workspace_id == self.workspace_id)
        if summary_id is not None:
            if resource != "attachments":
                raise OperationsError(
                    400, "Summary filtering applies only to attachments."
                )
            self._load("summaries", summary_id)
            statement = statement.where(table.c.summary_id == summary_id)
        if summary_date is not None:
            if resource != "summaries":
                raise OperationsError(400, "Date filtering applies only to summaries.")
            statement = statement.where(table.c.summary_date == summary_date)
        if project_id is not None:
            if resource in TOP_LEVEL_RESOURCES:
                raise OperationsError(400, "This resource has no project filter.")
            self._load("projects", project_id)
            statement = statement.where(table.c.project_id == project_id)
        if not archived:
            statement = statement.where(table.c.archived_at.is_(None))
            if resource == "attachments":
                project, summary = self.tables.projects, self.tables.summaries
                statement = statement.where(
                    select(project.c.id)
                    .where(
                        project.c.id == table.c.project_id,
                        project.c.workspace_id == self.workspace_id,
                        project.c.archived_at.is_(None),
                    )
                    .exists()
                    | select(summary.c.id)
                    .where(
                        summary.c.id == table.c.summary_id,
                        summary.c.workspace_id == self.workspace_id,
                        summary.c.archived_at.is_(None),
                    )
                    .exists()
                )
            elif resource not in TOP_LEVEL_RESOURCES:
                project = self.tables.projects
                statement = statement.join(
                    project,
                    (project.c.id == table.c.project_id)
                    & (project.c.workspace_id == table.c.workspace_id),
                ).where(project.c.archived_at.is_(None))
        statement = (
            statement.order_by(
                *([table.c.summary_date.desc()] if resource == "summaries" else []),
                table.c.updated_at.desc(),
                table.c.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return [
            serialize(dict(row))
            for row in self.connection.execute(statement).mappings()
        ]

    def _labor_values(
        self, attributes: Record, *, require_assignment: bool = True
    ) -> Record:
        """Validate hours against the existing worker assignment."""
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
        return {}

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

    def _active_parent(self, resource: Resource, record: Record) -> None:
        """Lock and validate the parent before touching a child."""
        if resource == "attachments" and record.get("summary_id"):
            parent = self._load(
                "summaries", cast(UUID, record["summary_id"]), lock=True
            )
            if parent["archived_at"] is not None:
                raise OperationsError(
                    409, "Restore the summary before changing its links."
                )
        else:
            self._active_project(cast(UUID, record["project_id"]))

    def _touch_parent(
        self, resource: Resource, record: Record, occurred_at: datetime
    ) -> None:
        """Advance parent revisions for attachment and other child changes."""
        if resource == "attachments" and record.get("summary_id"):
            table = self.tables.summaries
            self.connection.execute(
                table.update()
                .where(
                    table.c.workspace_id == self.workspace_id,
                    table.c.id == record["summary_id"],
                )
                .values(updated_at=occurred_at, revision=table.c.revision + 1)
            )
        else:
            self._touch_project(cast(UUID, record["project_id"]), occurred_at)

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
            return serialize(cast(Record, previous["result"]))
        occurred_at = datetime.now(timezone.utc)
        if resource == "summaries":
            attributes["source"] = "manual"
        if resource not in TOP_LEVEL_RESOURCES:
            self._active_parent(resource, attributes)
            if resource == "tasks":
                attributes.update(completed=False, completed_at=None)
            elif resource == "labor_entries":
                attributes.update(self._labor_values(attributes))
            elif resource == "assignments":
                self._assignment_worker(attributes)
            self._touch_parent(resource, attributes, occurred_at)
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
        if resource not in TOP_LEVEL_RESOURCES:
            # Lock parent before child in every mutation to avoid archive/write races.
            task = self._load(resource, record_id)
            self._active_parent(resource, task)
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
        if resource not in TOP_LEVEL_RESOURCES:
            self._touch_parent(resource, before, occurred_at)
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

    def summary(self, day: date | None = None) -> Record:
        """Calculate daily task counts, open jobs, and total recorded labor hours."""
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
        labor = self.tables.labor_entries
        total_minutes = self.connection.scalar(
            select(func.coalesce(func.sum(labor.c.minutes), 0))
            .select_from(labor)
            .join(
                projects,
                (projects.c.id == labor.c.project_id)
                & (projects.c.workspace_id == labor.c.workspace_id),
            )
            .where(
                labor.c.workspace_id == self.workspace_id,
                labor.c.archived_at.is_(None),
                projects.c.archived_at.is_(None),
            )
        )
        return {
            "total_labor_hours": float(total_minutes or 0) / 60,
            "day": day.isoformat(),
            "timezone": self.business_timezone,
            "items_today": total,
            "completed_today": completed,
            "open_jobs": open_jobs,
            "tasks": today_tasks,
            "tasks_has_more": cast(int, total) > 100,
        }
