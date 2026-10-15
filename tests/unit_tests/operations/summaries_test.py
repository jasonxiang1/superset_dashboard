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

from datetime import date, datetime, timezone
from typing import cast
from uuid import UUID, uuid4

import pytest
from marshmallow import ValidationError
from sqlalchemy import Engine

from superset.operations.repository import OperationsError, OperationsRepository, Record
from superset.operations.schemas import SummaryCreateSchema
from superset.operations.tables import OperationsTables


def test_summaries_persist_filter_scope_and_recover(
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> None:
    """Manual and external updates share date filtering, scoping and audit behavior."""
    workspace = uuid4()
    payload = cast(
        Record,
        SummaryCreateSchema().load(
            {
                "request_id": str(uuid4()),
                "summary_date": "2026-10-11",
                "title": "Site update",
                "body": "Crew arrived; inspection planned.",
            }
        ),
    )
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "17")
        first = repo.create("summaries", payload)
        assert repo.create("summaries", payload) == first
        assert first["source"] == "manual"
        repo.create(
            "summaries", {**payload, "request_id": uuid4(), "title": "Afternoon update"}
        )
        repo.create(
            "summaries",
            {**payload, "request_id": uuid4(), "summary_date": date(2026, 10, 12)},
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            operations_tables.summaries.insert().values(
                id=uuid4(),
                workspace_id=workspace,
                revision=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
                summary_date=date(2026, 10, 11),
                title="Imported status",
                body="Updated from business PostgreSQL.",
                source="postgresql",
            )
        )
    record_id = UUID(str(first["id"]))
    with operations_engine.begin() as connection:
        repo = OperationsRepository(connection, operations_tables, workspace, "18")
        updates = repo.list_records(
            "summaries",
            archived=False,
            limit=100,
            offset=0,
            summary_date=date(2026, 10, 11),
        )
        assert len(updates) == 3
        assert {row["source"] for row in updates} == {"manual", "postgresql"}
        foreign = OperationsRepository(connection, operations_tables, uuid4(), "19")
        assert (
            foreign.list_records(
                "summaries",
                archived=False,
                limit=100,
                offset=0,
                summary_date=date(2026, 10, 11),
            )
            == []
        )
        with pytest.raises(OperationsError, match="Record not found"):
            foreign.get("summaries", record_id)
        updated = repo.change(
            "summaries",
            record_id,
            {"revision": 1, "body": "Inspection passed."},
            "edit",
        )
        assert updated["body"] == "Inspection passed."
        assert updated["source"] == "manual"
        with pytest.raises(OperationsError, match="Record changed"):
            repo.change(
                "summaries", record_id, {"revision": 1, "body": "Stale"}, "edit"
            )
        repo.change("summaries", record_id, {"revision": 2}, "archive")
        assert (
            len(
                repo.list_records(
                    "summaries",
                    archived=False,
                    limit=100,
                    offset=0,
                    summary_date=date(2026, 10, 11),
                )
            )
            == 2
        )
        assert (
            len(
                repo.list_records(
                    "summaries",
                    archived=True,
                    limit=100,
                    offset=0,
                    summary_date=date(2026, 10, 11),
                )
            )
            == 3
        )
        repo.change("summaries", record_id, {"revision": 3}, "restore")
        assert (
            len(
                repo.list_records(
                    "summaries",
                    archived=False,
                    limit=100,
                    offset=0,
                    summary_date=date(2026, 10, 11),
                )
            )
            == 3
        )
        assert len(repo.history("summaries", record_id, 0)) == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "postgresql"},
        {"title": " "},
        {"body": " "},
        {"summary_date": "invalid"},
        {"body": "x" * 8001},
    ],
)
def test_summary_schema_rejects_invalid_inputs(changes: Record) -> None:
    """Provenance and scope cannot be supplied by a manual API client."""
    with pytest.raises(ValidationError):
        SummaryCreateSchema().load(
            {
                "request_id": str(uuid4()),
                "summary_date": "2026-10-11",
                "title": "Status",
                "body": "Update",
                **changes,
            }
        )
