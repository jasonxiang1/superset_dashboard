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
"""Exercise actual FAB authentication, action permissions and business writes."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from flask import Flask, g
from flask.testing import FlaskClient
from pytest_mock import MockerFixture
from sqlalchemy import Engine

from superset.extensions import security_manager
from superset.operations.api import OperationsRestApi
from superset.operations.tables import OperationsTables

pytestmark = pytest.mark.parametrize(
    "app",
    [{"FEATURE_FLAGS": {"OPERATIONS_ENABLED": True}}],
    indirect=True,
)


@pytest.fixture
def workspace() -> UUID:
    """Allocate a workspace identifier independent of Superset metadata IDs."""
    return uuid4()


@pytest.fixture
def business_database(
    app: Flask,
    mocker: MockerFixture,
    workspace: UUID,
    operations_engine: Engine,
    operations_tables: OperationsTables,
) -> MagicMock:
    """Use a real PostgreSQL engine while substituting the unavailable deployment DB."""

    @contextmanager
    def engine_context() -> Iterator[Engine]:
        yield operations_engine

    app.config.update(
        OPERATIONS_DATABASE_UUID=str(uuid4()),
        OPERATIONS_SCHEMA=operations_tables.metadata.schema,
        OPERATIONS_WORKSPACE_ROLES={
            str(workspace): ["operations_editor", "operations_reader"]
        },
    )
    database = SimpleNamespace(allow_dml=True, get_sqla_engine=engine_context)
    mocker.patch(
        "superset.operations.api.DatabaseDAO.find_one_or_none", return_value=database
    )
    return mocker.patch.object(
        security_manager, "can_access_database", return_value=True
    )


def authenticated_client(
    app: Flask, role_name: str, permissions: list[str]
) -> FlaskClient:
    """Log in a real FAB user without bypassing route permission decorators."""
    unique_role_name = f"{role_name}_{uuid4().hex}"
    role = security_manager.add_role(unique_role_name)
    for allowed_roles in app.config["OPERATIONS_WORKSPACE_ROLES"].values():
        if role_name in allowed_roles:
            allowed_roles.append(unique_role_name)
    for permission in permissions:
        pvm = security_manager.add_permission_view_menu(permission, "Operations")
        security_manager.add_permission_role(role, pvm)
    user = security_manager.add_user(
        username=unique_role_name,
        first_name="Operations",
        last_name="Test",
        email=f"{unique_role_name}@example.com",
        role=role,
        password="test-only-password",  # noqa: S106
    )
    assert user
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    # The parent fixture holds an app context across requests and clients.
    g.pop("_login_user", None)
    return client


@pytest.fixture
def editor(app: Flask, business_database: MagicMock) -> FlaskClient:
    """A custom role receives only the explicitly granted operations capabilities."""
    return authenticated_client(
        app,
        "operations_editor",
        [
            "can_read",
            "can_create",
            "can_edit",
            "can_remove",
            "can_restore",
            "can_complete",
            "can_log_labor",
            "can_add_note",
            "can_assign",
        ],
    )


def base_url(workspace: UUID) -> str:
    """Build the API prefix using a public workspace UUID."""
    return f"/api/v1/operations/workspaces/{workspace}"


def create_project(client: FlaskClient, workspace: UUID) -> dict[str, object]:
    """Create a project through the registered HTTP route."""
    response = client.post(
        f"{base_url(workspace)}/projects",
        json={
            "request_id": str(uuid4()),
            "title": "Kitchen remodel",
        },
    )
    assert response.status_code == 201, response.json
    return response.json["result"]


def test_http_crud_completion_and_recovery(
    editor: FlaskClient, workspace: UUID
) -> None:
    """Registered endpoints persist project/task writes and reject stale revisions."""
    project = create_project(editor, workspace)
    response = editor.patch(
        f"{base_url(workspace)}/projects/{project['id']}",
        json={
            "revision": 1,
            "title": "Updated remodel",
            "status": "in_progress",
        },
    )
    assert response.status_code == 200
    assert (
        editor.patch(
            f"{base_url(workspace)}/projects/{project['id']}",
            json={
                "revision": 1,
                "title": "Stale edit",
            },
        ).status_code
        == 409
    )
    task = editor.post(
        f"{base_url(workspace)}/tasks",
        json={
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "title": "Confirm crew",
        },
    )
    assert task.status_code == 201
    task_url = f"{base_url(workspace)}/tasks/{task.json['result']['id']}"
    assert (
        editor.post(
            f"{task_url}/completion", json={"revision": 1, "completed": True}
        ).status_code
        == 200
    )
    assert editor.get(task_url).json["result"]["completed"] is True
    assert editor.post(f"{task_url}/archive", json={"revision": 2}).status_code == 200
    assert editor.get(f"{base_url(workspace)}/tasks").json["result"] == []
    assert editor.post(f"{task_url}/restore", json={"revision": 3}).status_code == 200
    assert editor.get(task_url).json["result"]["completed"] is True
    assert editor.delete(task_url).status_code == 405


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/projects", {"request_id": str(uuid4()), "title": "x"}),
        ("patch", "/projects/{id}", {"revision": 1, "title": "x"}),
        ("post", "/projects/{id}/archive", {"revision": 1}),
        ("post", "/projects/{id}/restore", {"revision": 1}),
        ("post", "/tasks/{id}/completion", {"revision": 1, "completed": True}),
    ],
)
def test_reader_cannot_mutate(
    app: Flask,
    editor: FlaskClient,
    workspace: UUID,
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    """Read access does not grant any of the separate mutation capabilities."""
    project = create_project(editor, workspace)
    reader = authenticated_client(app, "operations_reader", ["can_read"])
    assert reader.get(f"{base_url(workspace)}/projects").status_code == 200
    response = getattr(reader, method)(
        base_url(workspace) + path.format(id=project["id"]), json=payload
    )
    assert response.status_code == 403


def test_completion_permission_does_not_allow_general_edits(
    app: Flask,
    editor: FlaskClient,
    workspace: UUID,
) -> None:
    """A task-completion role cannot change its title, parent or other fields."""
    project = create_project(editor, workspace)
    task = editor.post(
        f"{base_url(workspace)}/tasks",
        json={
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "title": "Confirm crew",
        },
    ).json["result"]
    client = authenticated_client(
        app, "operations_reader", ["can_read", "can_complete"]
    )
    url = f"{base_url(workspace)}/tasks/{task['id']}"
    assert (
        client.post(
            f"{url}/completion", json={"revision": 1, "completed": True}
        ).status_code
        == 200
    )
    assert (
        client.patch(url, json={"revision": 2, "title": "Forbidden"}).status_code == 403
    )
    assert (
        client.post(
            f"{url}/completion",
            json={"revision": 2, "completed": False, "title": "Forbidden"},
        ).status_code
        == 400
    )


def test_workspace_denial_precedes_database_access(
    editor: FlaskClient,
    business_database: MagicMock,
) -> None:
    """An authenticated editor cannot select an ungranted workspace."""
    response = editor.get(f"{base_url(uuid4())}/projects")
    assert response.status_code == 403
    business_database.assert_not_called()


def test_database_grant_and_dml_are_required(
    editor: FlaskClient,
    workspace: UUID,
    business_database: MagicMock,
    mocker: MockerFixture,
) -> None:
    """Workspace membership cannot substitute for DB grants or write enablement."""
    business_database.return_value = False
    assert editor.get(f"{base_url(workspace)}/projects").status_code == 403
    business_database.return_value = True
    mocker.patch(
        "superset.operations.api.DatabaseDAO.find_one_or_none",
        return_value=SimpleNamespace(allow_dml=False),
    )
    assert (
        editor.post(
            f"{base_url(workspace)}/projects",
            json={
                "request_id": str(uuid4()),
                "title": "x",
            },
        ).status_code
        == 403
    )


@pytest.mark.parametrize("path", ["projects", "tasks"])
def test_anonymous_user_is_denied(app: Flask, workspace: UUID, path: str) -> None:
    """Registered routes reject unauthenticated clients."""
    with app.test_client() as client:
        assert client.get(f"{base_url(workspace)}/{path}").status_code in {401, 403}


def test_disabled_feature_and_missing_configuration(
    app: Flask,
    editor: FlaskClient,
    workspace: UUID,
    mocker: MockerFixture,
) -> None:
    """No mock fallback exists when disabled or disconnected."""
    enabled = mocker.patch(
        "superset.operations.api.feature_flag_manager.is_feature_enabled",
        return_value=False,
    )
    assert editor.get(f"{base_url(workspace)}/projects").status_code == 404
    enabled.return_value = True
    app.config["OPERATIONS_DATABASE_UUID"] = None
    assert editor.get(f"{base_url(workspace)}/projects").status_code == 503


@pytest.mark.parametrize(
    "query", ["limit=101", "offset=-1", "limit=x", "include_archived=1"]
)
def test_invalid_list_options(editor: FlaskClient, workspace: UUID, query: str) -> None:
    """Reject unbounded or ambiguous list requests."""
    assert editor.get(f"{base_url(workspace)}/projects?{query}").status_code == 400


def test_invalid_payload_resource_and_oversized_body(
    editor: FlaskClient, workspace: UUID
) -> None:
    """Reject arbitrary resources, malformed JSON, and oversized submissions."""
    url = f"{base_url(workspace)}/projects"
    assert editor.post(url, json=[]).status_code == 400
    assert (
        editor.post(url, data="bad", content_type="application/json").status_code == 400
    )
    assert (
        editor.post(
            url,
            json={
                "title": "x",
                "request_id": str(uuid4()),
                "workspace_id": str(uuid4()),
            },
        ).status_code
        == 400
    )
    assert (
        editor.post(
            url,
            json={"title": "x", "request_id": str(uuid4()), "description": "x" * 65536},
        ).status_code
        == 413
    )
    assert editor.get(f"{base_url(workspace)}/operations_audit").status_code == 404


def test_stock_roles_do_not_receive_operations_capabilities() -> None:
    """Role synchronization requires explicit business grants for Gamma and Alpha."""
    pvm = SimpleNamespace(
        permission=SimpleNamespace(name="can_edit"),
        view_menu=SimpleNamespace(name="Operations"),
    )
    assert not security_manager._is_gamma_pvm(pvm)
    assert not security_manager._is_alpha_pvm(pvm)
    assert "can_complete" in OperationsRestApi().base_permissions


def test_note_capability_is_independent_of_project_creation(
    app: Flask,
    editor: FlaskClient,
    workspace: UUID,
) -> None:
    """A note-only role can add notes but cannot create projects or labor records."""
    project = create_project(editor, workspace)
    client = authenticated_client(
        app, "operations_reader", ["can_read", "can_add_note"]
    )
    note = client.post(
        f"{base_url(workspace)}/notes",
        json={
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "body": "Client approved finish",
        },
    )
    assert note.status_code == 201
    assert (
        client.post(
            f"{base_url(workspace)}/projects",
            json={
                "request_id": str(uuid4()),
                "title": "Not permitted",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(f"{base_url(workspace)}/labor_entries", json={}).status_code == 403
    )
    assert (
        client.get(
            f"{base_url(workspace)}/notes/{note.json['result']['id']}/history"
        ).status_code
        == 403
    )
    capabilities = client.get("/api/v1/operations/settings").json["result"][
        "capabilities"
    ]
    assert capabilities["add_note"] is True
    assert capabilities["create"] is False


def test_directory_sources_and_history_are_protected(
    editor: FlaskClient,
    workspace: UUID,
) -> None:
    """Directory and attachment sources remain read-only for operations editors."""
    assert editor.post(f"{base_url(workspace)}/workers", json={}).status_code == 405
    assert editor.post(f"{base_url(workspace)}/attachments", json={}).status_code == 405
    assert (
        editor.post(
            f"{base_url(workspace)}/workers/{uuid4()}/archive", json={"revision": 1}
        ).status_code
        == 405
    )
    assert (
        editor.get(f"{base_url(workspace)}/tasks?project_id=invalid").status_code == 400
    )
    project = create_project(editor, workspace)
    response = editor.get(f"{base_url(workspace)}/projects/{project['id']}/history")
    assert response.status_code == 200
    assert response.json["result"][0]["action"] == "create"


def test_page_entry_points_and_settings_require_authentication(app: Flask) -> None:
    """Deep links and settings do not expose the application to anonymous users."""
    with app.test_client() as client:
        assert client.get("/api/v1/operations/settings").status_code in {401, 403}
        assert client.get("/operations/brief/").status_code in {302, 401, 403}
        assert client.get("/operations/projects/").status_code in {302, 401, 403}
