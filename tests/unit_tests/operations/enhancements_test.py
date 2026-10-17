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
"""Regression coverage for links, financial removal and approved chat changes."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from marshmallow import ValidationError
from pytest_mock import MockerFixture
from sqlalchemy import Engine, text

from superset.operations.chat import decide, validate_action
from superset.operations.migrate import upgrade
from superset.operations.repository import (
    OperationsError,
    OperationsRepository,
    serialize,
)
from superset.operations.schemas import AttachmentCreateSchema, SummaryCreateSchema
from superset.operations.tables import OperationsTables
from tests.unit_tests.operations.repository_test import project_payload


def test_financial_history_is_sanitized() -> None:
    """Legacy audit JSON cannot re-expose retired monetary fields."""
    assert serialize(
        {
            "before": {"cost": "25", "minutes": 60},
            "after": {"hourly_rate": "10", "note": "$10 in free text"},
        }
    ) == {"before": {"minutes": 60}, "after": {"note": "$10 in free text"}}


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"project_id": str(uuid4()), "summary_id": str(uuid4())},
        {"project_id": str(uuid4()), "url": "javascript:alert(1)"},
    ],
)
def test_attachment_invalid_ownership_or_url(fields: dict[str, str]) -> None:
    """Only HTTP(S) links with exactly one parent are accepted."""
    with pytest.raises(ValidationError):
        AttachmentCreateSchema().load(
            {
                "request_id": str(uuid4()),
                "name": "Photo",
                "url": "https://example.com/photo.png",
                **fields,
            }
        )


def test_summary_links_and_archive_scope(
    operations_engine: Engine, operations_tables: OperationsTables
) -> None:
    """Links follow summary lifecycle and cannot cross workspaces."""
    with operations_engine.begin() as conn:
        repo = OperationsRepository(conn, operations_tables, uuid4(), "1")
        summary = repo.create(
            "summaries",
            SummaryCreateSchema().load(
                {
                    "request_id": str(uuid4()),
                    "summary_date": "2026-10-16",
                    "title": "Site",
                    "body": "Complete",
                }
            ),
        )
        payload = AttachmentCreateSchema().load(
            {
                "request_id": str(uuid4()),
                "summary_id": summary["id"],
                "name": "Photo",
                "url": "https://example.com/photo.png",
            }
        )
        link = repo.create("attachments", payload)
        assert repo.create("attachments", payload) == link
        summary_id = UUID(str(summary["id"]))
        assert (
            len(
                repo.list_records(
                    "attachments",
                    archived=False,
                    limit=20,
                    offset=0,
                    summary_id=summary_id,
                )
            )
            == 1
        )
        repo.change("summaries", summary_id, {"revision": 2}, "archive")
        assert (
            repo.list_records("attachments", archived=False, limit=20, offset=0) == []
        )
        with pytest.raises(OperationsError, match="Restore the summary"):
            repo.change(
                "attachments", UUID(str(link["id"])), {"revision": 1}, "archive"
            )
        other = OperationsRepository(conn, operations_tables, uuid4(), "1")
        with pytest.raises(OperationsError, match="Record not found"):
            other.create("attachments", {**payload, "request_id": uuid4()})


def test_proposals_are_bound_atomic_and_replayed(
    operations_engine: Engine,
    operations_tables: OperationsTables,
    mocker: MockerFixture,
) -> None:
    """Only the approving actor/conversation can apply once; stale edits fail."""
    mocker.patch(
        "superset.operations.chat.security_manager", can_access=lambda *args: True
    )
    with operations_engine.begin() as conn:
        repo = OperationsRepository(conn, operations_tables, uuid4(), "1")
        record = repo.create("projects", project_payload())
        action = validate_action(
            repo,
            {
                "resource": "projects",
                "action": "edit",
                "id": record["id"],
                "payload": {"revision": 1, "title": "Approved title"},
            },
        )
        proposal_id, conversation = uuid4(), uuid4()
        conn.execute(
            operations_tables.proposals.insert().values(
                id=proposal_id,
                workspace_id=repo.workspace_id,
                actor_id="1",
                conversation_id=conversation,
                actions=[action],
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                cancelled=False,
            )
        )
        assert (
            repo.get("projects", UUID(str(record["id"])))["title"] != "Approved title"
        )
        with pytest.raises(OperationsError, match="not found"):
            decide(repo, proposal_id, {"conversation_id": uuid4(), "decision": "apply"})
        result = decide(
            repo, proposal_id, {"conversation_id": conversation, "decision": "apply"}
        )
        assert result["applied"]
        assert (
            decide(
                repo,
                proposal_id,
                {"conversation_id": conversation, "decision": "apply"},
            )
            == result
        )
        assert repo.get("projects", UUID(str(record["id"])))["revision"] == 2
        with pytest.raises(OperationsError, match="Record changed"):
            validate_action(repo, action)


def test_migration_is_repeatable_and_preserves_legacy_rows(
    operations_engine: Engine, operations_tables: OperationsTables
) -> None:
    """Exercise upgrade on a disposable legacy-shaped schema, never production."""
    schema = operations_tables.metadata.schema
    with operations_engine.begin() as conn:
        repo = OperationsRepository(conn, operations_tables, uuid4(), "1")
        project = repo.create("projects", project_payload())
        conn.execute(
            text(
                f'ALTER TABLE "{schema}".operations_attachments DROP '
                f"CONSTRAINT fk_operations_attachments_summary"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{schema}".operations_attachments DROP '
                f"CONSTRAINT ck_operations_attachment_parent"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{schema}".operations_attachments DROP COLUMN summary_id'
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{schema}".operations_attachments ALTER '
                f"COLUMN project_id SET NOT NULL"
            )
        )
        for column in ("hourly_rate", "cost", "currency"):
            conn.execute(
                text(
                    f'ALTER TABLE "{schema}".operations_labor_entries '
                    f"ALTER COLUMN {column} SET NOT NULL"
                )
            )
        upgrade(conn, schema)
        upgrade(conn, schema)
        assert repo.get("projects", UUID(str(project["id"]))) == project


@pytest.mark.parametrize("state", ["expired", "cancelled", "other_actor", "denied"])
def test_unusable_proposals_cannot_write(
    operations_engine: Engine,
    operations_tables: OperationsTables,
    mocker: MockerFixture,
    state: str,
) -> None:
    """Expired, cancelled, foreign and unauthorized approvals never execute."""
    mocker.patch(
        "superset.operations.chat.security_manager",
        can_access=lambda *args: state != "denied",
    )
    with operations_engine.begin() as conn:
        repo = OperationsRepository(conn, operations_tables, uuid4(), "viewer")
        proposal_id, conversation = uuid4(), uuid4()
        conn.execute(
            operations_tables.proposals.insert().values(
                id=proposal_id,
                workspace_id=repo.workspace_id,
                actor_id="someone_else" if state == "other_actor" else "viewer",
                conversation_id=conversation,
                actions=[],
                cancelled=state == "cancelled",
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=-1 if state == "expired" else 15),
            )
        )
        with pytest.raises(OperationsError):
            decide(
                repo,
                proposal_id,
                {"conversation_id": conversation, "decision": "apply"},
            )
        assert repo.list_records("projects", archived=False, limit=20, offset=0) == []
