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
"""Authenticated CRUD for explicitly configured operational workspaces."""

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import date
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from flask import current_app, g, request, Response
from flask_appbuilder.api import expose, permission_name, protect, safe
from marshmallow import Schema, ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from superset.daos.database import DatabaseDAO
from superset.extensions import feature_flag_manager, security_manager
from superset.operations.repository import (
    OperationsError,
    OperationsRepository,
    Record,
    Resource,
)
from superset.operations.schemas import (
    AssignmentCreateSchema,
    LaborCreateSchema,
    LaborUpdateSchema,
    NoteCreateSchema,
    NoteUpdateSchema,
    ProjectCreateSchema,
    ProjectUpdateSchema,
    RevisionSchema,
    SummaryCreateSchema,
    SummaryUpdateSchema,
    TaskCompletionSchema,
    TaskCreateSchema,
    TaskUpdateSchema,
)
from superset.operations.tables import OperationsTables
from superset.views.base_api import BaseSupersetApi

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 64 * 1024
READ_RESOURCES = {
    "projects",
    "tasks",
    "notes",
    "labor_entries",
    "assignments",
    "workers",
    "attachments",
    "summaries",
}
CREATE_SCHEMAS: dict[str, type[Schema]] = {
    "projects": ProjectCreateSchema,
    "tasks": TaskCreateSchema,
    "notes": NoteCreateSchema,
    "labor_entries": LaborCreateSchema,
    "assignments": AssignmentCreateSchema,
    "summaries": SummaryCreateSchema,
}
UPDATE_SCHEMAS: dict[str, type[Schema]] = {
    "projects": ProjectUpdateSchema,
    "tasks": TaskUpdateSchema,
    "notes": NoteUpdateSchema,
    "labor_entries": LaborUpdateSchema,
    "summaries": SummaryUpdateSchema,
}


def resource_name(value: str) -> Resource:
    """Allow only the explicitly implemented resources."""
    if value not in READ_RESOURCES:
        raise OperationsError(404, "Resource not found.")
    return cast(Resource, value)


def mutable_resource(value: str) -> Resource:
    """Keep directory and attachment source records read-only."""
    name = resource_name(value)
    if name not in CREATE_SCHEMAS:
        raise OperationsError(405, "This resource is read-only.")
    return name


def load_payload(schema: Schema) -> Record:
    """Require bounded JSON objects and reject unknown or immutable fields."""
    if not request.is_json:
        raise OperationsError(400, "A JSON object is required.")
    if request.content_length and request.content_length > MAX_REQUEST_BYTES:
        raise OperationsError(413, "Request is too large.")
    if len(request.get_data()) > MAX_REQUEST_BYTES:
        raise OperationsError(413, "Request is too large.")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise OperationsError(400, "A JSON object is required.")
    return cast(Record, schema.load(payload))


@contextmanager
def repository_for(workspace_id: UUID) -> Iterator[OperationsRepository]:
    """Check identity, DB grants and workspace roles before opening a transaction."""
    if not feature_flag_manager.is_feature_enabled("OPERATIONS_ENABLED"):
        raise OperationsError(404, "Operations is disabled.")
    if not g.user or not g.user.is_authenticated or security_manager.is_guest_user():
        raise OperationsError(403, "An authenticated operations user is required.")
    if not security_manager.can_access("can_read", "Operations"):
        raise OperationsError(403, "Operations read access is required.")
    workspaces = current_app.config["OPERATIONS_WORKSPACE_ROLES"]
    workspace_roles = workspaces.get(str(workspace_id))
    roles = {role.name for role in security_manager.get_user_roles()}
    if workspace_roles is None or (
        not security_manager.is_admin() and not roles.intersection(workspace_roles)
    ):
        raise OperationsError(403, "Workspace access denied.")
    database_uuid = current_app.config["OPERATIONS_DATABASE_UUID"]
    schema = current_app.config["OPERATIONS_SCHEMA"]
    if (
        not database_uuid
        or not isinstance(schema, str)
        or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema)
    ):
        raise OperationsError(503, "Operations database is not configured.")
    try:
        configured_uuid = UUID(str(database_uuid))
    except ValueError as ex:
        raise OperationsError(503, "Operations database is not configured.") from ex
    database = DatabaseDAO.find_one_or_none(uuid=configured_uuid)
    if database is None:
        raise OperationsError(503, "Operations database is unavailable.")
    if not security_manager.can_access_database(database):
        raise OperationsError(403, "Operations database access denied.")
    if request.method != "GET" and not database.allow_dml:
        raise OperationsError(403, "Operations database writes are disabled.")
    with cast(AbstractContextManager[Engine], database.get_sqla_engine()) as engine:
        with engine.begin() as connection:
            yield OperationsRepository(
                connection,
                OperationsTables(schema),
                workspace_id,
                str(g.user.id),
                business_timezone=current_app.config["OPERATIONS_TIMEZONE"],
                currency=current_app.config["OPERATIONS_CURRENCY"],
                financials_enabled=current_app.config["OPERATIONS_FINANCIALS_ENABLED"],
            )


class OperationsRestApi(BaseSupersetApi):
    """Read and mutate scoped business records with separate action capabilities."""

    resource_name = "operations"
    class_permission_name = "Operations"
    allow_browser_login = True
    base_permissions = [
        "can_read",
        "can_create",
        "can_edit",
        "can_remove",
        "can_restore",
        "can_complete",
        "can_log_labor",
        "can_add_note",
        "can_assign",
    ]
    openapi_spec_tag = "Operations"
    openapi_spec_component_schemas = (
        ProjectCreateSchema,
        ProjectUpdateSchema,
        TaskCreateSchema,
        TaskUpdateSchema,
        RevisionSchema,
        TaskCompletionSchema,
        NoteCreateSchema,
        NoteUpdateSchema,
        LaborCreateSchema,
        LaborUpdateSchema,
        AssignmentCreateSchema,
        SummaryCreateSchema,
        SummaryUpdateSchema,
    )

    def _run(
        self,
        workspace_id: UUID,
        action: Callable[[OperationsRepository], object],
        status: int = 200,
    ) -> Response:
        """Commit before returning success and translate failures without DB details."""
        try:
            with repository_for(workspace_id) as repository:
                result = action(repository)
            return self.response(status, result=result)
        except OperationsError as ex:
            return self.response(ex.status, message=str(ex))
        except ValidationError as ex:
            return self.response(
                400, message="Invalid operations request.", errors=ex.messages
            )
        except IntegrityError:
            return self.response(
                409, message="The change conflicts with an existing record."
            )
        except SQLAlchemyError:
            logger.warning("Operations database request failed")
            return self.response(503, message="Operations database is unavailable.")
        except ZoneInfoNotFoundError:
            return self.response(
                503, message="Operations timezone is not configured correctly."
            )

    @expose("/settings", methods=("GET",))
    @protect()
    @permission_name("read")
    @safe
    def settings(self) -> Response:
        """Expose authorized workspaces and action capabilities without credentials.
        ---
        get:
          summary: Read operations workspace settings
          responses:
            200:
              description: Workspaces and capabilities
        """
        if not feature_flag_manager.is_feature_enabled("OPERATIONS_ENABLED"):
            return self.response(404, message="Operations is disabled.")
        if (
            not g.user
            or not g.user.is_authenticated
            or security_manager.is_guest_user()
        ):
            return self.response(
                403, message="An authenticated operations user is required."
            )
        roles = {role.name for role in security_manager.get_user_roles()}
        names = current_app.config["OPERATIONS_WORKSPACE_NAMES"]
        workspaces = [
            {"id": workspace, "name": names.get(workspace, workspace)}
            for workspace, allowed in current_app.config[
                "OPERATIONS_WORKSPACE_ROLES"
            ].items()
            if security_manager.is_admin() or roles.intersection(allowed)
        ]
        return self.response(
            200,
            result={
                "workspaces": workspaces,
                "capabilities": {
                    action: security_manager.can_access(f"can_{action}", "Operations")
                    for action in (
                        "create",
                        "edit",
                        "remove",
                        "restore",
                        "complete",
                        "log_labor",
                        "add_note",
                        "assign",
                    )
                },
                "timezone": current_app.config["OPERATIONS_TIMEZONE"],
                "currency": current_app.config["OPERATIONS_CURRENCY"],
            },
        )

    @expose("/workspaces/<uuid:workspace_id>/brief", methods=("GET",))
    @protect()
    @permission_name("read")
    @safe
    def brief(self, workspace_id: UUID) -> Response:
        """Aggregate the brief from current business records.
        ---
        get:
          summary: Read daily brief
          responses:
            200:
              description: Daily tasks and summary metrics
        """
        return self._run(workspace_id, lambda repo: repo.summary())

    @expose("/workspaces/<uuid:workspace_id>/<resource>", methods=("GET",))
    @protect()
    @permission_name("read")
    @safe
    def list_records(self, workspace_id: UUID, resource: str) -> Response:
        """List projects or tasks, optionally including archived records.
        ---
        get:
          summary: List workspace operational records
          responses:
            200:
              description: Paginated current-state records
        """

        def read(repository: OperationsRepository) -> object:
            try:
                limit = int(request.args.get("limit", "50"))
                offset = int(request.args.get("offset", "0"))
                summary_date = (
                    date.fromisoformat(request.args["summary_date"])
                    if request.args.get("summary_date")
                    else None
                )
                project_id = (
                    UUID(request.args["project_id"])
                    if request.args.get("project_id")
                    else None
                )
            except ValueError as ex:
                raise OperationsError(400, "Invalid pagination.") from ex
            archived = request.args.get("include_archived", "false")
            if (
                not 1 <= limit <= 100
                or not 0 <= offset <= 100000
                or archived not in {"true", "false"}
            ):
                raise OperationsError(400, "Invalid list options.")
            return repository.list_records(
                resource_name(resource),
                archived=archived == "true",
                limit=limit,
                offset=offset,
                project_id=project_id,
                summary_date=summary_date,
            )

        return self._run(workspace_id, read)

    @expose(
        "/workspaces/<uuid:workspace_id>/<resource>/<uuid:record_id>", methods=("GET",)
    )
    @protect()
    @permission_name("read")
    @safe
    def get_record(
        self, workspace_id: UUID, resource: str, record_id: UUID
    ) -> Response:
        """Read a project or task from the authorized workspace.
        ---
        get:
          summary: Read an operational record
          responses:
            200:
              description: Current record state
        """
        return self._run(
            workspace_id, lambda repo: repo.get(resource_name(resource), record_id)
        )

    @expose("/workspaces/<uuid:workspace_id>/<resource>", methods=("POST",))
    @protect()
    @permission_name("read")
    @safe
    def create_record(self, workspace_id: UUID, resource: str) -> Response:
        """Create a project or task with an idempotent request UUID.
        ---
        post:
          summary: Create an operational record
          responses:
            201:
              description: Created record or original response on retry
        """

        def create(repository: OperationsRepository) -> object:
            name = resource_name(resource)
            if name not in CREATE_SCHEMAS:
                raise OperationsError(405, "This resource is read-only.")
            capability = {
                "labor_entries": "log_labor",
                "notes": "add_note",
                "assignments": "assign",
            }.get(name, "create")
            if not security_manager.can_access(f"can_{capability}", "Operations"):
                raise OperationsError(403, "This action is not permitted.")
            schema = CREATE_SCHEMAS[name]()
            return repository.create(name, load_payload(schema))

        return self._run(workspace_id, create, 201)

    @expose(
        "/workspaces/<uuid:workspace_id>/<resource>/<uuid:record_id>",
        methods=("PATCH",),
    )
    @protect()
    @permission_name("edit")
    @safe
    def update_record(
        self, workspace_id: UUID, resource: str, record_id: UUID
    ) -> Response:
        """Edit allowed fields using the last observed revision.
        ---
        patch:
          summary: Edit an operational record
          responses:
            200:
              description: Updated record
            409:
              description: Stale revision or archived record
        """

        def update(repository: OperationsRepository) -> object:
            name = resource_name(resource)
            if name not in UPDATE_SCHEMAS:
                raise OperationsError(405, "This resource cannot be edited.")
            schema = UPDATE_SCHEMAS[name]()
            return repository.change(name, record_id, load_payload(schema), "edit")

        return self._run(workspace_id, update)

    @expose(
        "/workspaces/<uuid:workspace_id>/<resource>/<uuid:record_id>/archive",
        methods=("POST",),
    )
    @protect()
    @permission_name("remove")
    @safe
    def archive_record(
        self, workspace_id: UUID, resource: str, record_id: UUID
    ) -> Response:
        """Recoverably remove a record without deleting its history or children.
        ---
        post:
          summary: Archive an operational record
          responses:
            200:
              description: Archived record
        """
        return self._run(
            workspace_id,
            lambda repo: repo.change(
                mutable_resource(resource),
                record_id,
                load_payload(RevisionSchema()),
                "archive",
            ),
        )

    @expose(
        "/workspaces/<uuid:workspace_id>/<resource>/<uuid:record_id>/restore",
        methods=("POST",),
    )
    @protect()
    @permission_name("restore")
    @safe
    def restore_record(
        self, workspace_id: UUID, resource: str, record_id: UUID
    ) -> Response:
        """Restore an archived record with conflict detection.
        ---
        post:
          summary: Restore an operational record
          responses:
            200:
              description: Restored record
        """
        return self._run(
            workspace_id,
            lambda repo: repo.change(
                mutable_resource(resource),
                record_id,
                load_payload(RevisionSchema()),
                "restore",
            ),
        )

    @expose(
        "/workspaces/<uuid:workspace_id>/tasks/<uuid:record_id>/completion",
        methods=("POST",),
    )
    @protect()
    @permission_name("complete")
    @safe
    def complete_task(self, workspace_id: UUID, record_id: UUID) -> Response:
        """Complete or reopen a task through its separate capability.
        ---
        post:
          summary: Complete or reopen a task
          responses:
            200:
              description: Updated task
        """
        return self._run(
            workspace_id,
            lambda repo: repo.change(
                "tasks", record_id, load_payload(TaskCompletionSchema()), "complete"
            ),
        )

    @expose(
        "/workspaces/<uuid:workspace_id>/<resource>/<uuid:record_id>/history",
        methods=("GET",),
    )
    @protect()
    @permission_name("edit")
    @safe
    def history(self, workspace_id: UUID, resource: str, record_id: UUID) -> Response:
        """Return audit history only to an editor of the authorized workspace.
        ---
        get:
          summary: Read operational record history
          responses:
            200:
              description: Authorized audit snapshots
        """

        def read(repository: OperationsRepository) -> object:
            try:
                offset = int(request.args.get("offset", "0"))
            except ValueError as ex:
                raise OperationsError(400, "Invalid history offset.") from ex
            if not 0 <= offset <= 100000:
                raise OperationsError(400, "Invalid history offset.")
            return repository.history(mutable_resource(resource), record_id, offset)

        return self._run(workspace_id, read)
