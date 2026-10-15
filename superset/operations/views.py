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
"""Authenticated HTML entry points for the Operations application."""

from flask import abort
from flask_appbuilder import expose, has_access

from superset.extensions import feature_flag_manager, security_manager
from superset.superset_typing import FlaskResponse
from superset.views.base import BaseSupersetView


class OperationsView(BaseSupersetView):
    """Serve the existing app shell without replacing any Superset route."""

    route_base = "/operations"
    class_permission_name = "Operations"
    method_permission_name = {"brief": "read", "projects": "read"}

    def _render(self) -> FlaskResponse:
        """Require the feature and an authenticated, non-guest principal."""
        if not feature_flag_manager.is_feature_enabled("OPERATIONS_ENABLED"):
            abort(404)
        if security_manager.is_guest_user():
            abort(403)
        return super().render_app_template()

    @expose("/brief/")
    @has_access
    def brief(self) -> FlaskResponse:
        """Open the daily brief."""
        return self._render()

    @expose("/projects/")
    @has_access
    def projects(self) -> FlaskResponse:
        """Open projects."""
        return self._render()
