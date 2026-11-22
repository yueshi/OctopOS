"""GlobalProcessor stamps verified owner identity for bound DMs only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from octop_gateway.models import ChannelSubject, InboundMessage, TextContent

from octop.infra.gateway.process.processor import GlobalProcessor
from octop.infra.gateway.slash.dispatcher import SlashDispatcher


class _IdentityRepo:
    def __init__(self, bindings: set[tuple[str, str, int]]) -> None:
        self._bindings = bindings

    def get(self, channel_type: str, sender_id: str, *, account_key: str = ""):
        for kind, subject, user_id in self._bindings:
            if (kind, subject) == (channel_type, sender_id):
                return SimpleNamespace(user_id=user_id)
        return None


def _processor(identity_repo) -> GlobalProcessor:
    return GlobalProcessor(
        agent_manager=MagicMock(),
        thread_registry=MagicMock(),
        audit_repo=MagicMock(),
        agent_repo=MagicMock(get=MagicMock(return_value=None)),
        user_repo=MagicMock(),
        connector_repo=MagicMock(),
        channel_identity_repo=identity_repo,
        dispatcher=SlashDispatcher(),
        gateway=None,
    )


def _msg(*, subject: str, chat_type: str = "dm") -> InboundMessage:
    return InboundMessage(
        channel_type="weixin",
        channel_id="ch1",
        channel_subject=ChannelSubject(subject_id=subject, chat_type=chat_type),
        content=[TextContent(text="hi")],
        text="hi",
        metadata={"chat_type": chat_type},
    )


def _agent(owner_id: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=owner_id, agent_id="agent-1")


def test_bound_owner_dm_is_verified() -> None:
    processor = _processor(_IdentityRepo({("weixin", "wx-owner", 7)}))
    assert (
        processor._channel_identity(_msg(subject="wx-owner"), _agent(7))["owner_verified"] is True
    )


def test_bound_non_owner_has_sender_identity_but_no_owner_proof() -> None:
    processor = _processor(_IdentityRepo({("weixin", "wx-member", 8)}))
    identity = processor._channel_identity(_msg(subject="wx-member"), _agent(7))
    assert identity["sender_user_id"] == 8
    assert identity["owner_verified"] is False
    assert identity["identity_verified"] is False


def test_unbound_and_group_are_not_verified() -> None:
    processor = _processor(_IdentityRepo({("weixin", "wx-owner", 7)}))
    assert (
        processor._channel_identity(_msg(subject="wx-other"), _agent(7))["owner_verified"] is False
    )
    assert (
        processor._channel_identity(_msg(subject="wx-owner", chat_type="group"), _agent(7))[
            "owner_verified"
        ]
        is False
    )


def test_binding_to_another_user_is_not_verified() -> None:
    processor = _processor(_IdentityRepo({("weixin", "wx-member", 8)}))
    assert (
        processor._channel_identity(_msg(subject="wx-member"), _agent(7))["owner_verified"] is False
    )


def test_missing_repo_is_not_verified() -> None:
    processor = _processor(None)
    assert (
        processor._channel_identity(_msg(subject="wx-owner"), _agent(7))["owner_verified"] is False
    )
