"""HTTP export of an app's plan as a CAD drawing."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from octop.infra.apps.service import AppService
from octop.infra.apps.wiring import build_app_service
from octop.infra.server import OctopServer

FIXTURE = (
    Path(__file__).resolve().parents[1] / "unit" / "design" / "fixtures" / "two_bedroom_81.json"
)

ROOM_COLUMNS = [
    {"name": "room_id", "label": "房间 ID", "type": "string", "required": True},
    {"name": "name", "label": "房间名", "type": "string"},
    {"name": "area_mm2", "label": "面积 (mm²)", "type": "number"},
    {"name": "payload", "label": "多边形", "type": "text"},
    {"name": "source", "label": "来源", "type": "string"},
]


def _service(srv: OctopServer) -> AppService:
    assert srv.services is not None
    assert srv.app_runtime is not None
    return build_app_service(
        repos=srv.services.repos,
        workspaces=srv.app_runtime.agent_registry,
    )


async def _seed_plan(srv: OctopServer, agent_id: str, username: str) -> str:
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    service = _service(srv)
    assert srv.services is not None
    owner = srv.services.user_repo.get_by_username(username)
    assert owner is not None
    row = await service.create_from_template(
        agent_id=agent_id,
        owner_user_id=owner.id,
        template="interior-plan",
        source="system",
        name="89㎡ 两居",
        grant_template_capabilities=True,
    )
    await service.create_table(row, table_key="rooms", label="房间", columns=ROOM_COLUMNS)
    for kind, key in (
        ("wall", "walls"),
        ("opening", "openings"),
        ("furniture", "furniture"),
        ("mep", "mep"),
    ):
        for item in plan.get(key, []):
            await service.append_row(
                row,
                {
                    "element_id": item["id"],
                    "kind": kind,
                    "name": item.get("name", ""),
                    "payload": json.dumps(item, ensure_ascii=False),
                    "source": "fixture",
                    "status": "confirmed",
                },
                table="main",
            )
    for item in plan.get("rooms", []):
        await service.append_row(
            row,
            {
                "room_id": item["id"],
                "name": item.get("name", ""),
                "payload": json.dumps({"polygon": item["polygon"]}, ensure_ascii=False),
                "source": "fixture",
            },
            table="rooms",
        )
    return row.app_id


async def test_export_dxf_returns_an_attachment(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, bob_auth, agent_id = env_alice_bob_agent
    app_id = await _seed_plan(srv, agent_id, "alice")

    r = await client.get(f"/api/apps/{app_id}/drawing", headers=alice_auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/dxf")
    assert "attachment" in r.headers["content-disposition"]
    assert ".dxf" in r.headers["content-disposition"]
    assert b"0\nSECTION" in r.content[:20]

    # The drawing carries the same data anyone with view access may read,
    # so a user without access gets the usual permission error.
    r = await client.get(f"/api/apps/{app_id}/drawing", headers=bob_auth)
    assert r.status_code == 403


async def test_export_dwg_without_oda_explains_the_requirement(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _seed_plan(srv, agent_id, "alice")
    monkeypatch.delenv("OCTOP_ODA_CONVERTER", raising=False)

    r = await client.get(f"/api/apps/{app_id}/drawing?format=dwg", headers=alice_auth)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "APP_EXPORT_UNAVAILABLE"
    assert "ODA File Converter" in r.json()["error"]["message"]


async def test_unknown_export_format_is_rejected(
    env_alice_bob_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], dict[str, str], str],
) -> None:
    client, srv, alice_auth, _bob_auth, agent_id = env_alice_bob_agent
    app_id = await _seed_plan(srv, agent_id, "alice")
    r = await client.get(f"/api/apps/{app_id}/drawing?format=pdf", headers=alice_auth)
    assert r.status_code == 422
