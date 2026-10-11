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
"""Canonical adapter tables; creating them is an explicit operator/test action."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.schema import SchemaItem

PROJECT_STATUSES = ("planned", "in_progress", "on_hold", "completed", "cancelled")


class OperationsTables:
    """Describe external tables without adding models to Superset's metadata DB."""

    def __init__(self, schema: str) -> None:
        self.metadata = MetaData(schema=schema)
        self.projects = Table(
            "operations_projects",
            self.metadata,
            *self._record_columns(),
            Column("title", String(200), nullable=False),
            Column("description", Text, nullable=False),
            Column("status", String(20), nullable=False),
            UniqueConstraint("workspace_id", "id"),
            CheckConstraint("revision > 0"),
            CheckConstraint(
                "status IN ('planned', 'in_progress', 'on_hold', "
                "'completed', 'cancelled')"
            ),
        )
        self.tasks = Table(
            "operations_tasks",
            self.metadata,
            *self._record_columns(),
            Column("project_id", Uuid, nullable=False),
            Column("title", String(200), nullable=False),
            Column("description", Text, nullable=False),
            Column("assignee_name", String(200)),
            Column("due_at", DateTime(timezone=True)),
            Column("completed", Boolean, nullable=False),
            Column("completed_at", DateTime(timezone=True)),
            ForeignKeyConstraint(
                ["workspace_id", "project_id"],
                [self.projects.c.workspace_id, self.projects.c.id],
            ),
            CheckConstraint("revision > 0"),
        )
        self.workers = Table(
            "operations_workers",
            self.metadata,
            *self._record_columns(),
            Column("name", String(200), nullable=False),
            UniqueConstraint("workspace_id", "id"),
        )
        self.rates = Table(
            "operations_worker_rates",
            self.metadata,
            Column("id", Uuid, primary_key=True),
            Column("workspace_id", Uuid, nullable=False),
            Column("worker_id", Uuid, nullable=False),
            Column("effective_from", Date, nullable=False),
            Column("hourly_rate", Numeric(12, 4), nullable=False),
            Column("currency", String(3), nullable=False),
            ForeignKeyConstraint(
                ["workspace_id", "worker_id"],
                [self.workers.c.workspace_id, self.workers.c.id],
            ),
            UniqueConstraint("workspace_id", "worker_id", "effective_from"),
            CheckConstraint("hourly_rate >= 0"),
        )
        self.notes = self._project_table(
            "operations_notes",
            Column("body", Text, nullable=False),
        )
        self.assignments = self._project_table(
            "operations_assignments",
            Column("worker_id", Uuid, nullable=False),
            ForeignKeyConstraint(
                ["workspace_id", "worker_id"],
                [self.workers.c.workspace_id, self.workers.c.id],
            ),
        )
        Index(
            "ix_operations_active_assignment",
            self.assignments.c.workspace_id,
            self.assignments.c.project_id,
            self.assignments.c.worker_id,
            unique=True,
            postgresql_where=self.assignments.c.archived_at.is_(None),
        )
        self.labor_entries = self._project_table(
            "operations_labor_entries",
            Column("worker_id", Uuid, nullable=False),
            Column("work_date", Date, nullable=False),
            Column("minutes", Integer, nullable=False),
            Column("note", Text, nullable=False),
            Column("hourly_rate", Numeric(12, 4), nullable=False),
            Column("cost", Numeric(18, 2), nullable=False),
            Column("currency", String(3), nullable=False),
            ForeignKeyConstraint(
                ["workspace_id", "worker_id"],
                [self.workers.c.workspace_id, self.workers.c.id],
            ),
            CheckConstraint("minutes > 0 AND minutes <= 1440"),
            CheckConstraint("hourly_rate >= 0 AND cost >= 0"),
        )
        self.financials = Table(
            "operations_project_financials",
            self.metadata,
            Column("workspace_id", Uuid, primary_key=True),
            Column("project_id", Uuid, primary_key=True),
            Column("revenue", Numeric(18, 2), nullable=False),
            Column("non_labor_cost", Numeric(18, 2), nullable=False),
            Column("client_receivable", Numeric(18, 2), nullable=False),
            Column("labor_paid", Numeric(18, 2), nullable=False),
            Column("currency", String(3), nullable=False),
            ForeignKeyConstraint(
                ["workspace_id", "project_id"],
                [self.projects.c.workspace_id, self.projects.c.id],
            ),
        )
        self.attachments = self._project_table(
            "operations_attachments",
            Column("name", String(200), nullable=False),
            Column("url", Text, nullable=False),
        )
        self.audit = Table(
            "operations_audit",
            self.metadata,
            Column("id", Uuid, primary_key=True),
            Column("workspace_id", Uuid, nullable=False),
            Column("resource", String(20), nullable=False),
            Column("record_id", Uuid, nullable=False),
            Column("actor_id", String(100), nullable=False),
            Column("action", String(20), nullable=False),
            Column("occurred_at", DateTime(timezone=True), nullable=False),
            Column("before", JSON),
            Column("after", JSON, nullable=False),
        )
        self.requests = Table(
            "operations_requests",
            self.metadata,
            Column("workspace_id", Uuid, primary_key=True),
            Column("request_id", Uuid, primary_key=True),
            Column("actor_id", String(100), nullable=False),
            Column("fingerprint", String(64), nullable=False),
            Column("result", JSON, nullable=False),
        )
        for table in (
            self.projects,
            self.tasks,
            self.notes,
            self.assignments,
            self.labor_entries,
            self.workers,
            self.attachments,
        ):
            Index(f"ix_{table.name}_activity", table.c.workspace_id, table.c.updated_at)

    def _project_table(self, name: str, *columns: SchemaItem) -> Table:
        """Keep child records in the same workspace as their project."""
        return Table(
            name,
            self.metadata,
            *self._record_columns(),
            Column("project_id", Uuid, nullable=False),
            *columns,
            ForeignKeyConstraint(
                ["workspace_id", "project_id"],
                [self.projects.c.workspace_id, self.projects.c.id],
            ),
            CheckConstraint("revision > 0"),
        )

    @staticmethod
    def _record_columns() -> list[Column[UUID] | Column[int] | Column[datetime]]:
        """Return independent columns for each operational resource."""
        return [
            Column("id", Uuid, primary_key=True),
            Column("workspace_id", Uuid, nullable=False),
            Column("revision", Integer, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            Column("archived_at", DateTime(timezone=True)),
        ]
