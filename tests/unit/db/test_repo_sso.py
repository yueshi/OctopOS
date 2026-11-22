"""Tests for OIDC SSO repository access."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.sso import SsoRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "sso.db")
    run_migrations(pool)
    return pool


def test_login_code_single_consume(db: SqlitePool) -> None:
    repo = SsoRepo(db)
    now = int(time.time())
    provider = repo.upsert_provider(
        enabled=True,
        display_name="OneID",
        issuer="https://idp.example.com",
        client_id="client-id",
        client_secret_enc=b"encrypted-secret",
        scopes="openid profile email",
        dashboard_origin="https://octop.example.com",
    )
    repo.create_login_state(
        state="state-1",
        provider_id=provider.id,
        nonce="nonce-1",
        code_verifier="verifier-1",
        redirect_after="/chat",
        expires_at=now + 60,
    )
    repo.attach_login_code(
        "state-1",
        login_code="login-code-1",
        user_id=1,
        expires_at=now + 60,
    )

    assert repo.consume_login_code("login-code-1") == {"user_id": 1}
    assert repo.consume_login_code("login-code-1") is None


def test_login_codes_require_oidc_state_proof(db: SqlitePool) -> None:
    """A row written without nonce/PKCE did not come from the IdP callback.

    This is the signature of an agent-generated script that minted a full
    admin login link straight from the database: neither attaching nor
    consuming a code may work for such a state.
    """
    repo = SsoRepo(db)
    now = int(time.time())
    provider = repo.upsert_provider(
        enabled=True,
        display_name="OneID",
        issuer="https://idp.example.com",
        client_id="client-id",
        client_secret_enc=b"encrypted-secret",
        scopes="openid profile email",
        dashboard_origin="https://octop.example.com",
    )
    repo.create_login_state(
        state="synthetic",
        provider_id=provider.id,
        nonce="",
        code_verifier="",
        redirect_after="/apps/app_1",
        expires_at=now + 600,
    )
    repo.attach_login_code(
        "synthetic",
        login_code="attached-code",
        user_id=1,
        expires_at=now + 600,
    )
    assert repo.consume_login_code("attached-code") is None

    # Even a direct SQL write (bypassing attach_login_code) cannot be used.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE sso_login_states SET login_code = ?, user_id = ?, expires_at = ? "
            "WHERE state = ?",
            ("forged-code", 1, now + 600, "synthetic"),
        )
    assert repo.consume_login_code("forged-code") is None


def test_provider_state_lookup_and_expiry_cleanup(db: SqlitePool) -> None:
    repo = SsoRepo(db)
    now = int(time.time())
    provider = repo.upsert_provider(
        enabled=True,
        display_name="OneID",
        issuer="https://idp.example.com",
        client_id="client-id",
        client_secret_enc=b"encrypted-secret",
        scopes="openid profile email",
        dashboard_origin="https://octop.example.com",
    )
    updated = repo.upsert_provider(
        enabled=False,
        display_name="Updated OneID",
        issuer="https://idp.example.com",
        client_id="client-id-2",
        client_secret_enc=None,
        scopes="openid",
        dashboard_origin=None,
    )
    repo.create_login_state(
        state="active-state",
        provider_id=provider.id,
        nonce="nonce-1",
        code_verifier="verifier-1",
        redirect_after="/chat",
        expires_at=now + 60,
    )
    repo.create_login_state(
        state="expired-state",
        provider_id=provider.id,
        nonce="nonce-2",
        code_verifier="verifier-2",
        redirect_after="/chat",
        expires_at=now,
    )

    assert repo.get_provider() == updated
    assert updated.kind == "oidc"
    first_id = updated.id
    feishu = repo.upsert_by_kind(
        "feishu",
        enabled=True,
        display_name="Feishu",
        issuer="",
        client_id="cli_xxx",
        client_secret_enc=b"secret",
        scopes="",
        dashboard_origin=None,
        extra={"region": "feishu"},
    )
    again = repo.upsert_by_kind(
        "feishu",
        enabled=False,
        display_name="Feishu 2",
        issuer="",
        client_id="cli_yyy",
        client_secret_enc=None,
        scopes="",
        dashboard_origin=None,
        extra={"region": "lark"},
    )
    assert repo.get_provider() == updated
    assert again.id == feishu.id
    assert again.enabled == 0
    assert again.extra["region"] == "lark"
    assert repo.get_by_kind("oidc").id == first_id
    assert updated.client_secret_enc == b"encrypted-secret"
    claimed = repo.take_login_state("active-state")
    assert claimed is not None
    assert claimed.state == "active-state"
    assert claimed.consumed_at is not None
    assert repo.take_login_state("active-state") is None
    assert repo.take_login_state("expired-state") is None

    repo.delete_expired(now)

    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT state FROM sso_login_states WHERE state = ?", ("expired-state",)
            ).fetchone()
            is None
        )
