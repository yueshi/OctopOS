"""Scoped workspace preview tokens."""

from __future__ import annotations

import time

import jwt
import pytest

from octop.infra.agents.workspace.preview_tokens import (
    WORKSPACE_PREVIEW_TOKEN_TYPE,
    create_workspace_preview_token,
    decode_workspace_preview_token,
)

SECRET = b"preview-test-secret" * 2


def test_roundtrip_keeps_scope_claims() -> None:
    token, expires_at = create_workspace_preview_token(
        SECRET,
        agent_id="agt_1",
        user_id=7,
        root="outbound",
        as_user=9,
        is_admin=True,
        ttl_seconds=600,
    )
    payload = decode_workspace_preview_token(SECRET, token)
    assert payload["typ"] == WORKSPACE_PREVIEW_TOKEN_TYPE
    assert payload["agent_id"] == "agt_1"
    assert payload["user_id"] == 7
    assert payload["root"] == "outbound"
    assert payload["as_user"] == 9
    assert payload["admin"] is True
    assert expires_at - int(time.time()) >= 500


def test_as_user_defaults_to_none() -> None:
    token, _ = create_workspace_preview_token(
        SECRET,
        agent_id="agt_1",
        user_id=7,
        root=".",
    )
    payload = decode_workspace_preview_token(SECRET, token)
    assert payload["as_user"] is None
    assert payload["admin"] is False


def test_rejects_expired_token() -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "typ": WORKSPACE_PREVIEW_TOKEN_TYPE,
            "agent_id": "agt_1",
            "user_id": 7,
            "root": ".",
            "iat": now - 120,
            "exp": now - 60,
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_workspace_preview_token(SECRET, token)


def test_rejects_wrong_token_type() -> None:
    token = jwt.encode(
        {
            "typ": "access",
            "agent_id": "agt_1",
            "user_id": 7,
            "root": ".",
            "exp": int(time.time()) + 60,
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        decode_workspace_preview_token(SECRET, token)


def test_rejects_missing_claims() -> None:
    token = jwt.encode(
        {
            "typ": WORKSPACE_PREVIEW_TOKEN_TYPE,
            "agent_id": "agt_1",
            "user_id": 7,
            "exp": int(time.time()) + 60,
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        decode_workspace_preview_token(SECRET, token)


def test_token_has_no_sub_claim() -> None:
    """A preview token must not be replayable as a dashboard access token."""
    token, _ = create_workspace_preview_token(
        SECRET,
        agent_id="agt_1",
        user_id=7,
        root=".",
    )
    payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert "sub" not in payload
