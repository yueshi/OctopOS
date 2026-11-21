"""Pre-approval validation and conditional HITL for app tools."""

from __future__ import annotations

from types import SimpleNamespace

from octop.infra.apps.hitl_preflight import approval_needed


def _req(name: str, args: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args})


class _Ingress:
    def __init__(self, *, app_id: str = "app_1", mode: str = "app.inbox") -> None:
        self.app_id = app_id
        self.mode = mode


class _IngressRepo:
    def __init__(self, row: _Ingress | None = None, *, raises: bool = False) -> None:
        self.row = row
        self.raises = raises

    def get(self, ingress_id: str) -> _Ingress | None:
        if self.raises:
            raise RuntimeError("db down")
        return self.row if ingress_id == "ing_1" else None


def test_table_visibility_only_asks_when_exposing() -> None:
    args = {"app_id": "app_1", "table": "main"}
    assert approval_needed(_req("app_set_table_visibility", {**args, "public": False})) is False
    assert approval_needed(_req("app_set_table_visibility", {**args, "public": "false"})) is False
    assert approval_needed(_req("app_set_table_visibility", {**args, "public": True})) is True
    assert approval_needed(_req("app_set_table_visibility", {**args, "public": "true"})) is True
    # Missing argument keeps the card (fail closed).
    assert approval_needed(_req("app_set_table_visibility", args)) is True


def test_integration_updates_only_skip_a_pure_disable() -> None:
    ingress = {"app_id": "app_1", "ingress_id": "ing_1"}
    assert (
        approval_needed(_req("app_integration_update_ingress", {**ingress, "enabled": False}))
        is False
    )
    assert (
        approval_needed(_req("app_integration_update_ingress", {**ingress, "enabled": "false"}))
        is False
    )
    # A disable bundled with a data-ingress change still asks; the later
    # re-enable card would not show the earlier mapping change.
    assert (
        approval_needed(
            _req(
                "app_integration_update_ingress",
                {**ingress, "enabled": False, "mapping": {"table": "orders"}},
            )
        )
        is True
    )
    assert (
        approval_needed(_req("app_integration_update_ingress", {**ingress, "enabled": True}))
        is True
    )
    assert (
        approval_needed(_req("app_integration_update_ingress", {**ingress, "enabled": "true"}))
        is True
    )
    assert approval_needed(_req("app_integration_update_ingress", ingress)) is True

    webhook = {"app_id": "app_1", "webhook_id": "wh_1"}
    assert (
        approval_needed(_req("app_integration_update_webhook", {**webhook, "enabled": False}))
        is False
    )
    assert (
        approval_needed(
            _req(
                "app_integration_update_webhook",
                {**webhook, "enabled": False, "url": "https://example.test/hook"},
            )
        )
        is True
    )
    assert (
        approval_needed(_req("app_integration_update_webhook", {**webhook, "enabled": True}))
        is True
    )
    assert approval_needed(_req("app_integration_update_webhook", webhook)) is True


def test_test_ingress_skips_inbox_and_missing_targets() -> None:
    inbox = _IngressRepo(_Ingress(mode="app.inbox"))
    assert (
        approval_needed(
            _req("app_integration_test_ingress", {"app_id": "app_1", "ingress_id": "ing_1"}),
            ingress_repo=inbox,
        )
        is False
    )
    for mode in ("app.import", "app.refresh"):
        repo = _IngressRepo(_Ingress(mode=mode))
        assert (
            approval_needed(
                _req(
                    "app_integration_test_ingress",
                    {"app_id": "app_1", "ingress_id": "ing_1"},
                ),
                ingress_repo=repo,
            )
            is True
        )
    # Unknown id / wrong app fails inside the tool before any side effect.
    missing = _IngressRepo(_Ingress(mode="app.import"))
    assert (
        approval_needed(
            _req("app_integration_test_ingress", {"app_id": "app_1", "ingress_id": "ing_2"}),
            ingress_repo=missing,
        )
        is False
    )
    other_app = _IngressRepo(_Ingress(app_id="app_other", mode="app.import"))
    assert (
        approval_needed(
            _req("app_integration_test_ingress", {"app_id": "app_1", "ingress_id": "ing_1"}),
            ingress_repo=other_app,
        )
        is False
    )
    # No repo or a failed lookup keeps the card.
    assert (
        approval_needed(
            _req("app_integration_test_ingress", {"app_id": "app_1", "ingress_id": "ing_1"}),
        )
        is True
    )
    broken = _IngressRepo(raises=True)
    assert (
        approval_needed(
            _req("app_integration_test_ingress", {"app_id": "app_1", "ingress_id": "ing_1"}),
            ingress_repo=broken,
        )
        is True
    )


def test_add_service_skips_missing_connector_or_bad_provider() -> None:
    # provider=connector requires an existing instance.
    assert (
        approval_needed(
            _req(
                "app_add_service",
                {
                    "app_id": "app_1",
                    "name": "douban",
                    "provider": "connector",
                    "allowed_tools": ["search_books"],
                },
            )
        )
        is False
    )
    assert (
        approval_needed(
            _req(
                "app_add_service",
                {
                    "app_id": "app_1",
                    "name": "douban",
                    "provider": "connector",
                    "connector_instance_id": "conn_1",
                    "allowed_tools": ["search_books"],
                },
            )
        )
        is True
    )
    # Public sources auto-provision, so provider=external needs no instance.
    assert (
        approval_needed(
            _req(
                "app_add_service",
                {
                    "app_id": "app_1",
                    "name": "douban",
                    "provider": "external",
                    "connector_kind": "douban",
                    "allowed_tools": ["search_books"],
                },
            )
        )
        is True
    )
    assert (
        approval_needed(
            _req(
                "app_add_service",
                {
                    "app_id": "app_1",
                    "name": "douban",
                    "provider": "nope",
                    "allowed_tools": ["search_books"],
                },
            )
        )
        is False
    )
    assert (
        approval_needed(
            _req(
                "app_add_service",
                {"app_id": "app_1", "name": "douban", "provider": "external"},
            )
        )
        is False
    )


def test_import_dry_run_is_read_only_and_skips_approval() -> None:
    assert (
        approval_needed(
            _req(
                "app_import_rows",
                {"app_id": "app_1", "path": "inbound/x.csv", "dry_run": True},
            )
        )
        is False
    )
    assert (
        approval_needed(
            _req(
                "app_import_rows",
                {"app_id": "app_1", "path": "inbound/x.csv", "dry_run": False},
            )
        )
        is True
    )
    # A truthy string must not bypass the real-import approval.
    assert (
        approval_needed(
            _req(
                "app_import_rows",
                {"app_id": "app_1", "path": "inbound/x.csv", "dry_run": "true"},
            )
        )
        is True
    )


def test_unknown_tools_keep_the_card_and_missing_ids_skip_it() -> None:
    assert approval_needed(None) is True
    assert approval_needed(_req("app_delete", {"app_id": "app_1"})) is True
    assert approval_needed(_req("app_set_capabilities", {"app_id": "app_1"})) is True
    # Missing app_id: the tool will reject it before any write, so no card.
    assert approval_needed({"name": "app_add_service", "args": {}}) is False
