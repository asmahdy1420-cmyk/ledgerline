from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from ledgerline.api import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app("sqlite://")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_factory(client: TestClient):
    return client.app.state.session_factory


@pytest.fixture
def make_account(client: TestClient) -> Callable[..., dict[str, Any]]:
    def _make(code: str, type: str = "asset", currency: str = "USD") -> dict[str, Any]:
        resp = client.post(
            "/accounts",
            json={"code": code, "name": code.title(), "type": type, "currency": currency},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make
