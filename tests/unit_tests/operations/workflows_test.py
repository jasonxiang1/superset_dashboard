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
"""PostgreSQL checks for labor economics, notes, assignments and summary scope."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from superset.operations.repository import OperationsError, OperationsRepository, Record
from superset.operations.schemas import (
    AssignmentCreateSchema,
    LaborCreateSchema,
    NoteCreateSchema,
    TaskCreateSchema,
)
from superset.operations.tables import OperationsTables
from tests.unit_tests.operations.repository_test import project_payload


def seed_worker(repo: OperationsRepository, name: str = "Alex") -> UUID:
    """Populate a read-only directory source exclusively in the test schema."""
    worker = uuid4()
    now = datetime.now(timezone.utc)
    repo.connection.execute(
        repo.tables.workers.insert().values(
            id=worker,
            workspace_id=repo.workspace_id,
            name=name,
            revision=1,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
    )
    for effective, rate in [
        (date(2026, 1, 1), Decimal("20.25")),
        (date(2026, 10, 11), Decimal("30")),
    ]:
        repo.connection.execute(
            repo.tables.rates.insert().values(
                id=uuid4(),
                workspace_id=repo.workspace_id,
                worker_id=worker,
                effective_from=effective,
                hourly_rate=rate,
                currency="USD",
            )
        )
    return worker


def assignment_payload(project: Record, worker: UUID) -> Record:
    """Build a validated assignment submission."""
    return cast(
        Record,
        AssignmentCreateSchema().load(
            {
                "request_id": str(uuid4()),
                "project_id": project["id"],
                "worker_id": str(worker),
            }
        ),
    )


def labor_payload(project: Record, worker: UUID, day: str = "2026-10-10") -> Record:
    """Build a validated hour-log submission without accepting a rate from the user."""
    return cast(
        Record,
        LaborCreateSchema().load(
            {
                "request_id": str(uuid4()),
                "project_id": project["id"],
                "worker_id": str(worker),
                "work_date": day,
                "minutes": 90,
                "note": "Framing",
            }
        ),
    )


def test_historical_rate_snapshot_corrections_void_and_restore(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Changes to the rate source cannot silently rewrite an existing labor cost."""
    workspace = uuid4()
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "17")
        project = repo.create("projects", project_payload())
        worker = seed_worker(repo)
        with pytest.raises(OperationsError, match="Assign the worker"):
            repo.create("labor_entries", labor_payload(project, worker))
        repo.create("assignments", assignment_payload(project, worker))
        entry = repo.create("labor_entries", labor_payload(project, worker))
        entry_id = UUID(str(entry["id"]))
        assert Decimal(str(entry["cost"])) == Decimal("30.38")
        assert Decimal(str(entry["hourly_rate"])) == Decimal("20.25")
        connection.execute(
            operations_tables.rates.update()
            .where(
                operations_tables.rates.c.worker_id == worker,
                operations_tables.rates.c.effective_from == date(2026, 1, 1),
            )
            .values(hourly_rate=99)
        )
        entry = repo.change(
            "labor_entries",
            entry_id,
            {
                "revision": 1,
                "minutes": 60,
                "worker_id": worker,
                "work_date": date(2026, 10, 10),
            },
            "edit",
        )
        assert Decimal(str(entry["cost"])) == Decimal("20.25")
        entry = repo.change(
            "labor_entries",
            entry_id,
            {"revision": 2, "work_date": date(2026, 10, 11)},
            "edit",
        )
        assert Decimal(str(entry["cost"])) == Decimal("30.00")
        entry = repo.change("labor_entries", entry_id, {"revision": 3}, "archive")
        assert (
            repo.list_records("labor_entries", archived=False, limit=100, offset=0)
            == []
        )
        entry = repo.change("labor_entries", entry_id, {"revision": 4}, "restore")
        assert Decimal(str(entry["cost"])) == Decimal("30.00")
    with operations_engine.connect() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "18")
        assert Decimal(str(repo.get("labor_entries", entry_id)["cost"])) == Decimal(
            "30.00"
        )


def test_notes_edit_archive_recover_and_project_filter(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Notes are stored as literal text, scoped and recoverable."""
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, uuid4(), "17")
        project = repo.create("projects", project_payload())
        note = repo.create(
            "notes",
            cast(
                Record,
                NoteCreateSchema().load(
                    {
                        "request_id": str(uuid4()),
                        "project_id": project["id"],
                        "body": "<script>alert('note')</script>",
                    }
                ),
            ),
        )
        note_id = UUID(str(note["id"]))
        note = repo.change(
            "notes", note_id, {"revision": 1, "body": "Client approved finish"}, "edit"
        )
        repo.change("notes", note_id, {"revision": 2}, "archive")
        assert repo.list_records("notes", archived=False, limit=100, offset=0) == []
        repo.change("notes", note_id, {"revision": 3}, "restore")
        assert repo.get("notes", note_id)["body"] == "Client approved finish"
        other = repo.create("projects", project_payload("Other project"))
        assert (
            repo.list_records(
                "notes",
                archived=False,
                limit=100,
                offset=0,
                project_id=UUID(str(other["id"])),
            )
            == []
        )
        assert len(repo.history("notes", note_id)) == 4


def test_missing_or_cross_workspace_worker_and_rate_are_rejected(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """A directory identity must be authorized, assigned and have a dated rate."""
    with operations_engine.begin() as connection:
        first = OperationsRepository(connection, operations_tables, uuid4(), "17")
        other_worker = seed_worker(first)
        repo = OperationsRepository(connection, operations_tables, uuid4(), "18")
        project = repo.create("projects", project_payload())
        with pytest.raises(OperationsError, match="Record not found"):
            repo.create("assignments", assignment_payload(project, other_worker))
        worker = seed_worker(repo)
        repo.create("assignments", assignment_payload(project, worker))
        with pytest.raises(OperationsError, match="No labor rate"):
            repo.create("labor_entries", labor_payload(project, worker, "2025-12-31"))
        connection.execute(
            operations_tables.rates.update()
            .where(
                operations_tables.rates.c.worker_id == worker,
            )
            .values(currency="EUR")
        )
        with pytest.raises(OperationsError, match="currency"):
            repo.create("labor_entries", labor_payload(project, worker))


def test_summary_timezone_dst_and_archived_project_scope(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """A 23-hour business day uses local midnight boundaries, not UTC dates."""
    with operations_engine.begin() as connection:
        repo = OperationsRepository(
            connection,
            operations_tables,
            uuid4(),
            "17",
            business_timezone="America/Los_Angeles",
        )
        project = repo.create("projects", project_payload())
        for deadline in [
            "2026-03-08T08:00:00Z",
            "2026-03-09T06:59:59Z",
            "2026-03-09T07:00:00Z",
            "2026-03-08T07:59:59Z",
        ]:
            task = repo.create(
                "tasks",
                cast(
                    Record,
                    TaskCreateSchema().load(
                        {
                            "request_id": str(uuid4()),
                            "project_id": project["id"],
                            "title": "Due task",
                            "due_at": deadline,
                        }
                    ),
                ),
            )
            if deadline == "2026-03-08T08:00:00Z":
                repo.change(
                    "tasks",
                    UUID(str(task["id"])),
                    {"revision": 1, "completed": True},
                    "complete",
                )
        summary = repo.summary(date(2026, 3, 8))
        assert summary["items_today"] == 2
        assert summary["completed_today"] == 1
        assert summary["open_jobs"] == 1
        assert summary["labor_due"] is None
        assert summary["margin_percent"] is None
        project_id = UUID(str(project["id"]))
        revision = repo.get("projects", project_id)["revision"]
        repo.change("projects", project_id, {"revision": revision}, "archive")
        assert repo.summary(date(2026, 3, 8))["items_today"] == 0
        assert repo.summary()["open_jobs"] == 0


def test_financials_recalculate_voided_labor_and_preserve_archived_costs(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Incomplete sources are unavailable; complete sources drive due and margin."""
    with operations_engine.begin() as connection:
        repo = OperationsRepository(
            connection, operations_tables, uuid4(), "17", financials_enabled=True
        )
        project = repo.create("projects", project_payload())
        worker = seed_worker(repo)
        repo.create("assignments", assignment_payload(project, worker))
        entry = repo.create("labor_entries", labor_payload(project, worker))
        connection.execute(
            operations_tables.financials.insert().values(
                workspace_id=repo.workspace_id,
                project_id=UUID(str(project["id"])),
                revenue=1000,
                non_labor_cost=100,
                client_receivable=500,
                labor_paid=5,
                currency="USD",
            )
        )
        summary = repo.summary()
        assert Decimal(str(summary["labor_due"])) == Decimal("25.38")
        assert Decimal(str(summary["margin_percent"])) == Decimal("86.96")
        project_id = UUID(str(project["id"]))
        repo.change(
            "projects",
            project_id,
            {"revision": repo.get("projects", project_id)["revision"]},
            "archive",
        )
        assert repo.summary()["labor_due"] == summary["labor_due"]
        repo.change(
            "projects",
            project_id,
            {"revision": repo.get("projects", project_id)["revision"]},
            "restore",
        )
        repo.change("labor_entries", UUID(str(entry["id"])), {"revision": 1}, "archive")
        assert Decimal(str(repo.summary()["labor_due"])) == Decimal("0")
        assert Decimal(str(repo.summary()["margin_percent"])) == Decimal("90")
        repo.create("projects", project_payload("Missing financial snapshot"))
        assert repo.summary()["margin_percent"] is None


def test_duration_correction_after_unassignment_retains_rate(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Correct historical hours without requiring a worker to remain assigned."""
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, uuid4(), "17")
        project = repo.create("projects", project_payload())
        worker = seed_worker(repo)
        assignment = repo.create("assignments", assignment_payload(project, worker))
        entry = repo.create("labor_entries", labor_payload(project, worker))
        repo.change(
            "assignments", UUID(str(assignment["id"])), {"revision": 1}, "archive"
        )
        revised = repo.change(
            "labor_entries",
            UUID(str(entry["id"])),
            {"revision": 1, "minutes": 60},
            "edit",
        )
        assert Decimal(str(revised["cost"])) == Decimal("20.25")
        assert revised["hourly_rate"] == entry["hourly_rate"]
