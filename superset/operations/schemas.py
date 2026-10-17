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
"""Validate operational writes before acquiring a business DB connection."""

from marshmallow import (
    fields,
    pre_load,
    Schema,
    validate,
    validates_schema,
    ValidationError,
)

from superset.operations.tables import PROJECT_STATUSES


def nonblank(value: str) -> None:
    """Reject labels containing only whitespace."""
    if not value.strip():
        raise ValidationError("Enter a nonblank title.")


class ProjectCreateSchema(Schema):
    """Fields accepted when creating a project."""

    request_id = fields.UUID(required=True)
    title = fields.String(required=True, validate=[validate.Length(max=200), nonblank])
    description = fields.String(load_default="", validate=validate.Length(max=8000))
    status = fields.String(
        load_default="planned", validate=validate.OneOf(PROJECT_STATUSES)
    )


class RevisionSchema(Schema):
    """Require the revision the user viewed before changing a record."""

    revision = fields.Integer(
        required=True, strict=True, validate=validate.Range(min=1)
    )


class UpdateSchema(RevisionSchema):
    """Require at least one editable field in addition to the viewed revision."""

    @validates_schema
    def require_changes(self, data: dict[str, object], **kwargs: object) -> None:
        """Reject patches that contain only a revision."""
        if len(data) == 1:
            raise ValidationError("Provide at least one field to update.")


class ProjectUpdateSchema(UpdateSchema):
    """Only editable fields may be patched; identity and scope are immutable."""

    title = fields.String(validate=[validate.Length(max=200), nonblank])
    description = fields.String(validate=validate.Length(max=8000))
    status = fields.String(validate=validate.OneOf(PROJECT_STATUSES))


class TaskCreateSchema(Schema):
    """Fields accepted when creating a project-linked task."""

    request_id = fields.UUID(required=True)
    project_id = fields.UUID(required=True)
    title = fields.String(required=True, validate=[validate.Length(max=200), nonblank])
    description = fields.String(load_default="", validate=validate.Length(max=8000))
    assignee_name = fields.String(
        allow_none=True, load_default=None, validate=validate.Length(max=200)
    )
    due_at = fields.AwareDateTime(allow_none=True, load_default=None)


class SummaryCreateSchema(Schema):
    """Create a dated plain-text summary; provenance is assigned server-side."""

    request_id = fields.UUID(required=True)
    summary_date = fields.Date(required=True)
    title = fields.String(required=True, validate=[validate.Length(max=200), nonblank])
    body = fields.String(required=True, validate=[validate.Length(max=8000), nonblank])


class SummaryUpdateSchema(UpdateSchema):
    """Edit a summary without changing its identity or source."""

    summary_date = fields.Date()
    title = fields.String(validate=[validate.Length(max=200), nonblank])
    body = fields.String(validate=[validate.Length(max=8000), nonblank])


class TaskUpdateSchema(UpdateSchema):
    """Editable task fields; completion has a separately authorized endpoint."""

    title = fields.String(validate=[validate.Length(max=200), nonblank])
    description = fields.String(validate=validate.Length(max=8000))
    assignee_name = fields.String(allow_none=True, validate=validate.Length(max=200))
    due_at = fields.AwareDateTime(allow_none=True)


class TaskCompletionSchema(RevisionSchema):
    """Complete or reopen a task without granting general edit access."""

    completed = fields.Boolean(required=True, truthy={True}, falsy={False})

    @pre_load
    def require_boolean(
        self, data: dict[str, object], **kwargs: object
    ) -> dict[str, object]:
        """Accept JSON booleans rather than truthy strings or numeric aliases."""
        if "completed" in data and not isinstance(data["completed"], bool):
            raise ValidationError({"completed": ["A boolean is required."]})
        return data


class ProjectChildCreateSchema(Schema):
    """Require an idempotency key and immutable project link."""

    request_id = fields.UUID(required=True)
    project_id = fields.UUID(required=True)


class NoteCreateSchema(ProjectChildCreateSchema):
    """Create a plain-text project note."""

    body = fields.String(required=True, validate=[validate.Length(max=8000), nonblank])


class NoteUpdateSchema(UpdateSchema):
    """Edit only the text of a note."""

    body = fields.String(validate=[validate.Length(max=8000), nonblank])


class AssignmentCreateSchema(ProjectChildCreateSchema):
    """Assign an existing worker to a project."""

    worker_id = fields.UUID(required=True)


class LaborCreateSchema(AssignmentCreateSchema):
    """Log minutes without monetary fields."""

    work_date = fields.Date(required=True)
    minutes = fields.Integer(
        required=True, strict=True, validate=validate.Range(min=1, max=1440)
    )
    note = fields.String(load_default="", validate=validate.Length(max=8000))


class LaborUpdateSchema(UpdateSchema):
    """Correct labor inputs without accepting client-supplied rates or costs."""

    worker_id = fields.UUID()
    work_date = fields.Date()
    minutes = fields.Integer(strict=True, validate=validate.Range(min=1, max=1440))
    note = fields.String(validate=validate.Length(max=8000))


class AttachmentCreateSchema(Schema):
    """Store an external link belonging to exactly one project or summary."""

    request_id = fields.UUID(required=True)
    project_id = fields.UUID(allow_none=True, load_default=None)
    summary_id = fields.UUID(allow_none=True, load_default=None)
    name = fields.String(required=True, validate=[validate.Length(max=200), nonblank])
    url = fields.Url(
        required=True, schemes={"http", "https"}, validate=validate.Length(max=4096)
    )

    @validates_schema
    def require_parent(self, data: dict[str, object], **kwargs: object) -> None:
        """Reject missing or ambiguous ownership."""
        if bool(data.get("project_id")) == bool(data.get("summary_id")):
            raise ValidationError("Provide exactly one project or summary parent.")


class AttachmentUpdateSchema(UpdateSchema):
    """Edit link metadata without moving it between parents."""

    name = fields.String(validate=[validate.Length(max=200), nonblank])
    url = fields.Url(schemes={"http", "https"}, validate=validate.Length(max=4096))


class WorkerCreateSchema(Schema):
    """Create a workspace worker without financial attributes."""

    request_id = fields.UUID(required=True)
    name = fields.String(required=True, validate=[validate.Length(max=200), nonblank])


class WorkerUpdateSchema(UpdateSchema):
    """Rename a worker while preserving assignment and labor references."""

    name = fields.String(validate=[validate.Length(max=200), nonblank])
