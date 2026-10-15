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
"""Exercise actual PostgreSQL commits, locks, audit and recovery."""

from concurrent.futures import ThreadPoolExecutor
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from superset.operations.repository import OperationsError, OperationsRepository, Record
from superset.operations.schemas import ProjectCreateSchema, TaskCreateSchema
from superset.operations.tables import OperationsTables


def project_payload(title: str = "Kitchen remodel") -> Record:
    """Produce validated inputs rather than bypassing request defaults."""
    return cast(
        Record, ProjectCreateSchema().load({"request_id": str(uuid4()), "title": title})
    )


def task_payload(project_id: str) -> Record:
    """Create a task with a timezone-aware deadline."""
    return cast(
        Record,
        TaskCreateSchema().load(
            {
                "request_id": str(uuid4()),
                "project_id": project_id,
                "title": "Confirm crew",
                "due_at": "2026-10-10T09:00:00-07:00",
                "assignee_name": "Alex",
            }
        ),
    )


def test_commit_edit_complete_reopen_and_recover(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """State survives new connections and every change has a matching audit."""
    workspace = uuid4()
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "17")
        project = repo.create("projects", project_payload())
        task = repo.create("tasks", task_payload(str(project["id"])))
    task_id = UUID(str(task["id"]))
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "18")
        assert repo.get("tasks", task_id)["assignee_name"] == "Alex"
        task = repo.change(
            "tasks", task_id, {"revision": 1, "completed": True}, "complete"
        )
        assert task["completed"] is True
        assert task["completed_at"] is not None
        task = repo.change(
            "tasks", task_id, {"revision": 2, "completed": False}, "complete"
        )
        assert task["completed_at"] is None
        task = repo.change(
            "tasks", task_id, {"revision": 3, "title": "Call crew"}, "edit"
        )
        task = repo.change("tasks", task_id, {"revision": 4}, "archive")
        assert task["archived_at"] is not None
        assert repo.list_records("tasks", archived=False, limit=50, offset=0) == []
        assert len(repo.list_records("tasks", archived=True, limit=50, offset=0)) == 1
        task = repo.change("tasks", task_id, {"revision": 5}, "restore")
        assert task["title"] == "Call crew"
        assert task["archived_at"] is None
    with operations_engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(operations_tables.audit))
            == 7
        )
        repo = OperationsRepository(connection, operations_tables, workspace, "17")
        assert repo.get("tasks", task_id)["revision"] == 6
        assert repo.get("projects", UUID(str(project["id"])))["revision"] == 7


def test_workspace_scope_and_cross_workspace_parent(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Known UUIDs cannot read or link records in another workspace."""
    with operations_engine.begin() as connection:
        owner = OperationsRepository(connection, operations_tables, uuid4(), "17")
        project = owner.create("projects", project_payload())
    with operations_engine.begin() as connection:
        other = OperationsRepository(connection, operations_tables, uuid4(), "18")
        assert other.list_records("projects", archived=True, limit=50, offset=0) == []
        with pytest.raises(OperationsError, match="Record not found"):
            other.get("projects", UUID(str(project["id"])))
        with pytest.raises(OperationsError, match="Record not found"):
            other.change(
                "projects", UUID(str(project["id"])), {"revision": 1}, "archive"
            )
        with pytest.raises(OperationsError, match="Record not found"):
            other.create("tasks", task_payload(str(project["id"])))
    with operations_engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(operations_tables.tasks))
            == 0
        )


def test_archive_parent_blocks_changes_without_destroying_children(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Archived projects hide tasks but preserve them for restoration."""
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, uuid4(), "17")
        project = repo.create("projects", project_payload())
        task = repo.create("tasks", task_payload(str(project["id"])))
        project_id = UUID(str(project["id"]))
        repo.change("projects", project_id, {"revision": 2}, "archive")
        with pytest.raises(OperationsError, match="Restore the project"):
            repo.create("tasks", task_payload(str(project["id"])))
        with pytest.raises(OperationsError, match="Restore the project"):
            repo.change(
                "tasks",
                UUID(str(task["id"])),
                {"revision": 1, "completed": True},
                "complete",
            )
        assert repo.list_records("tasks", archived=False, limit=50, offset=0) == []
        repo.change("projects", project_id, {"revision": 3}, "restore")
        assert len(repo.list_records("tasks", archived=False, limit=50, offset=0)) == 1


def test_duplicate_submission_is_atomic_under_concurrency(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Simultaneous retries create one record and one audit, even before commit."""
    workspace, payload = uuid4(), project_payload()

    def submit() -> Record:
        with operations_engine.begin() as connection:
            return OperationsRepository(
                connection, operations_tables, workspace, "17"
            ).create("projects", payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0] == results[1]
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "17")
        with pytest.raises(OperationsError, match="another submission"):
            repo.create("projects", {**payload, "title": "Different project"})
        other_actor = OperationsRepository(
            connection, operations_tables, workspace, "18"
        )
        with pytest.raises(OperationsError, match="another submission"):
            other_actor.create("projects", payload)
        for table in (
            operations_tables.projects,
            operations_tables.audit,
            operations_tables.requests,
        ):
            assert connection.scalar(select(func.count()).select_from(table)) == 1


def test_concurrent_edits_detect_stale_revision(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Only one editor can save using a shared last-observed revision."""
    workspace = uuid4()
    with operations_engine.begin() as connection:
        project = OperationsRepository(
            connection, operations_tables, workspace, "17"
        ).create("projects", project_payload())

    def edit(title: str) -> int:
        try:
            with operations_engine.begin() as connection:
                OperationsRepository(
                    connection, operations_tables, workspace, "17"
                ).change(
                    "projects",
                    UUID(str(project["id"])),
                    {"revision": 1, "title": title},
                    "edit",
                )
            return 200
        except OperationsError as ex:
            return ex.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ["First edit", "Second edit"])) == [200, 409]
    with operations_engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(operations_tables.audit))
            == 2
        )


def test_audit_failure_rolls_back_record_request_and_parent_activity(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """An audit failure cannot leave a successful business write behind."""
    workspace = uuid4()
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "17")
        project = repo.create("projects", project_payload())

    def fail_audit() -> None:
        """Raise inside the transaction to exercise rollback on exit."""
        with operations_engine.begin() as connection:
            repo = OperationsRepository(connection, operations_tables, workspace, "17")
            with patch.object(
                repo, "_audit", side_effect=SQLAlchemyError("audit failed")
            ):
                repo.create("tasks", task_payload(str(project["id"])))

    with pytest.raises(SQLAlchemyError):
        fail_audit()
    with operations_engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(operations_tables.tasks))
            == 0
        )
        assert (
            connection.scalar(
                select(func.count()).select_from(operations_tables.requests)
            )
            == 1
        )
        repo = OperationsRepository(connection, operations_tables, workspace, "17")
        assert repo.get("projects", UUID(str(project["id"]))) == project


def test_user_text_is_data_and_list_is_paginated(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """SQL-looking strings stay literal and pagination limits the result."""
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, uuid4(), "17")
        title = "'); DROP TABLE operations_projects; --"
        project = repo.create("projects", project_payload(title))
        repo.create("projects", project_payload("Another project"))
        assert repo.get("projects", UUID(str(project["id"])))["title"] == title
        assert (
            len(repo.list_records("projects", archived=False, limit=1, offset=1)) == 1
        )
