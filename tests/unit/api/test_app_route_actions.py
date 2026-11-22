"""Every MiniApp HTTP route must declare its access action."""

from __future__ import annotations

from octop.api.routers import app_members, apps, guest
from octop.api.routers.app_route_actions import APP_ROUTE_ACTIONS
from octop.infra.apps.access import Action


def test_every_app_route_has_an_action_entry() -> None:
    route_names = {route.name for route in apps.router.routes}
    route_names |= {route.name for route in app_members.router.routes}
    route_names |= {route.name for route in guest.router.routes}
    assert route_names == set(APP_ROUTE_ACTIONS)


def test_action_values_are_enum_members_or_none() -> None:
    for name, action in APP_ROUTE_ACTIONS.items():
        assert action is None or isinstance(action, Action), name
