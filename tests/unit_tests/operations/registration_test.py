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
"""Default configuration leaves the existing Superset API surface unchanged."""

from uuid import uuid4

import pytest
from flask import Flask


@pytest.mark.parametrize(
    "app", [{"FEATURE_FLAGS": {"OPERATIONS_ENABLED": False}}], indirect=True
)
def test_disabled_feature_does_not_register_operations(app: Flask) -> None:
    """Operations routes do not exist unless the operator enables the feature."""
    with app.test_client() as client:
        response = client.get(f"/api/v1/operations/workspaces/{uuid4()}/projects")
    assert response.status_code == 404
