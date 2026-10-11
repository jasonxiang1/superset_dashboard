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
"""Validation protects identity, scope and capability-specific fields."""

from uuid import uuid4

import pytest
from marshmallow import Schema, ValidationError

from superset.operations.schemas import (
    ProjectCreateSchema,
    ProjectUpdateSchema,
    TaskCompletionSchema,
    TaskCreateSchema,
    TaskUpdateSchema,
)


@pytest.mark.parametrize(
    "schema,payload",
    [
        (ProjectCreateSchema(), {"request_id": str(uuid4()), "title": "  "}),
        (
            ProjectCreateSchema(),
            {"request_id": str(uuid4()), "title": "x", "status": "unknown"},
        ),
        (ProjectUpdateSchema(), {"revision": 1}),
        (
            ProjectUpdateSchema(),
            {"revision": 1, "title": "x", "workspace_id": str(uuid4())},
        ),
        (TaskUpdateSchema(), {"revision": 1, "completed": True}),
        (TaskUpdateSchema(), {"revision": 1, "status": "completed"}),
        (TaskUpdateSchema(), {"revision": 1, "project_id": str(uuid4())}),
        (TaskUpdateSchema(), {"revision": 1, "due_at": "2026-10-10T09:00:00"}),
        (TaskCompletionSchema(), {"revision": True, "completed": True}),
        (TaskCompletionSchema(), {"revision": "1", "completed": True}),
        (TaskCompletionSchema(), {"revision": 1, "completed": "true"}),
        (TaskCompletionSchema(), {"revision": 1, "completed": 1}),
        (TaskCompletionSchema(), {"revision": 1, "completed": True, "title": "x"}),
        (TaskCreateSchema(), {"request_id": str(uuid4()), "title": "x"}),
    ],
)
def test_invalid_or_immutable_fields(
    schema: Schema, payload: dict[str, object]
) -> None:
    """Reject changes outside the action's explicitly allowed fields."""
    with pytest.raises(ValidationError):
        schema.load(payload)


def test_task_deadline_requires_timezone_and_can_be_cleared() -> None:
    """Allow explicit clearing while preserving an aware parsed deadline."""
    parsed = TaskUpdateSchema().load(
        {"revision": 1, "due_at": "2026-10-10T09:00:00-07:00"}
    )
    assert parsed["due_at"].utcoffset() is not None
    assert TaskUpdateSchema().load({"revision": 1, "due_at": None})["due_at"] is None
