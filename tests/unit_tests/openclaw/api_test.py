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
import inspect
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import requests
from flask import Flask, g, Response

from superset.utils import json


def invoke(app: Flask, payload: object) -> Response:
    """Call the view body without FAB authorization for focused transport tests."""
    from superset.openclaw.api import OpenClawRestApi

    with app.test_request_context("/api/v1/openclaw/chat", json=payload):
        g.user = SimpleNamespace(id=17)
        return inspect.unwrap(OpenClawRestApi.chat)(OpenClawRestApi())


@pytest.fixture
def payload() -> dict[str, object]:
    """Provide a valid dashboard question."""
    return {
        "message": "What does revenue measure?",
        "conversation_id": str(uuid4()),
        "context": {"dashboard": {"dashboardId": 4}, "filterState": {}},
    }


@pytest.fixture
def gateway(app: Flask) -> Iterator[MagicMock]:
    """Replace network calls with a successful gateway response."""
    with (
        patch.dict(
            app.config,
            OPENCLAW_GATEWAY_URL="http://openclaw:18789",
            OPENCLAW_GATEWAY_TOKEN="test-gateway-token",  # noqa: S106
            OPENCLAW_AGENT_ID="jason",
        ),
        patch("superset.openclaw.api.requests.post") as post,
    ):
        upstream = post.return_value.__enter__.return_value
        upstream.status_code = 200
        upstream.iter_content.return_value = [
            json.dumps(
                {"choices": [{"message": {"content": "Revenue is total sales."}}]}
            ).encode()
        ]
        yield post


def test_missing_configuration_preserves_generic_error(
    app: Flask, payload: dict[str, object]
) -> None:
    """An unconfigured checkout returns the actionable disconnected message."""
    from superset.openclaw.api import CONNECTION_ERROR

    with patch.dict(app.config, OPENCLAW_GATEWAY_URL="", OPENCLAW_GATEWAY_TOKEN=""):
        with patch("superset.openclaw.api.requests.post") as post:
            response = invoke(app, payload)
    assert response.status_code == 503
    assert response.json == {"message": CONNECTION_ERROR}
    post.assert_not_called()


def test_gateway_receives_context_and_stable_conversation(
    app: Flask, payload: dict[str, object], gateway: MagicMock
) -> None:
    """Follow-ups reuse one session and a new conversation gets another session."""
    response = invoke(app, payload)
    assert response.status_code == 200
    assert response.json == {"result": "Revenue is total sales."}
    first = gateway.call_args.kwargs
    assert first["json"]["model"] == "openclaw/jason"
    assert first["json"]["user"] == f"superset:17:{payload['conversation_id']}"
    assert first["headers"] == {"Authorization": "Bearer test-gateway-token"}
    assert first["allow_redirects"] is False
    assert first["timeout"] == (5, 90)
    assert '"dashboardId": 4' in first["json"]["messages"][1]["content"]
    assert payload["message"] in first["json"]["messages"][1]["content"]
    assert "test-gateway-token" not in response.get_data(as_text=True)
    invoke(app, {**payload, "message": "And last month?"})
    assert gateway.call_args.kwargs["json"]["user"] == first["json"]["user"]
    invoke(app, {**payload, "conversation_id": str(uuid4())})
    assert gateway.call_args.kwargs["json"]["user"] != first["json"]["user"]


@pytest.mark.parametrize("failure", [requests.ConnectionError, requests.Timeout])
def test_network_errors_are_safe(
    app: Flask, payload: dict[str, object], gateway: MagicMock, failure: type[Exception]
) -> None:
    """Network details never leak into the user-visible error."""
    from superset.openclaw.api import CONNECTION_ERROR

    gateway.side_effect = failure("sensitive internal details")
    response = invoke(app, payload)
    assert response.status_code == 503
    assert response.json == {"message": CONNECTION_ERROR}
    assert gateway.call_count == 1


@pytest.mark.parametrize("status", [301, 401, 404, 429, 500])
def test_upstream_failures_are_not_forwarded(
    app: Flask, payload: dict[str, object], gateway: MagicMock, status: int
) -> None:
    """Redirects and gateway errors produce the same safe connection message."""
    gateway.return_value.__enter__.return_value.status_code = status
    assert invoke(app, payload).status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        b"not JSON",
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{"content":null}}]}',
        b"x" * (1024 * 1024 + 1),
    ],
)
def test_invalid_or_oversized_response(
    app: Flask, payload: dict[str, object], gateway: MagicMock, body: bytes
) -> None:
    """Malformed or excessive responses fail cleanly."""
    gateway.return_value.__enter__.return_value.iter_content.return_value = [body]
    assert invoke(app, payload).status_code == 503


@pytest.mark.parametrize(
    "updates",
    [
        {"message": " "},
        {"message": "x" * 8001},
        {"conversation_id": "main"},
        {"context": []},
        {"unexpected": True},
    ],
)
def test_invalid_request_does_not_reach_gateway(
    app: Flask,
    payload: dict[str, object],
    gateway: MagicMock,
    updates: dict[str, object],
) -> None:
    """Reject malformed questions and arbitrary session-routing values."""
    assert invoke(app, {**payload, **updates}).status_code == 400
    gateway.assert_not_called()


def test_large_context_does_not_reach_gateway(
    app: Flask, payload: dict[str, object], gateway: MagicMock
) -> None:
    """Bound dashboard snapshots before calling the gateway."""
    response = invoke(app, {**payload, "context": {"large": "x" * (256 * 1024)}})
    assert response.status_code == 413
    gateway.assert_not_called()


def test_chat_requires_authentication(app: Flask) -> None:
    """The registered HTTP route must reject an anonymous browser."""
    with app.test_client() as client:
        response = client.post("/api/v1/openclaw/chat", json={})
    assert response.status_code in {401, 403}
