"""Browser regression test for the interior-plan template page.

The page owns one contract with the geometry kernel: the IR it builds from the
``main`` table. Dropping a field there silently disables whole rule families
(the wardrobe clearance rule never fires without ``furniture[].kind``), and no
Python test can see it, so this drives the real page in Chromium.

The test skips when no Chromium build is installed, like the sandbox tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_miniapp_sandbox import _chromium_path

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "octop"
    / "infra"
    / "apps"
    / "system_templates"
    / "interior-plan"
    / "app.html"
)

# A stub bridge: enough of ``window.octopApp`` for the page to load, run a
# check, and write the diagnostics back. Everything it receives is recorded.
STUB_SDK = """<script>
window.__sent = [];
window.__appended = [];
window.__rows = {
  main: [
    { row_id: "r1", version: 1, values: {
      element_id: "W-001", kind: "wall", name: "南墙",
      payload: JSON.stringify({x1:0,y1:0,x2:4000,y2:0,t:120}), source: "t", status: "confirmed" } },
    { row_id: "r2", version: 1, values: {
      element_id: "D-001", kind: "opening", name: "入户门",
      payload: JSON.stringify({type:"door",x:2000,y:0,width:900,angle:0,host:"W-001"}),
      source: "t", status: "confirmed" } },
    { row_id: "r3", version: 1, values: {
      element_id: "F-001", kind: "furniture", name: "衣柜",
      payload: JSON.stringify({x:0,y:0,w:2000,d:600,rot:0,kind:"wardrobe"}),
      source: "t", status: "assumption" } },
    { row_id: "r5", version: 1, values: {
      element_id: "F-002", kind: "furniture", name: "橱柜台面",
      payload: JSON.stringify({x:3000,y:0,w:600,d:1800,rot:0,kind:"kitchen_counter"}),
      source: "t", status: "assumption" } },
    { row_id: "r6", version: 1, values: {
      element_id: "F-003", kind: "furniture", name: "水槽",
      payload: JSON.stringify({x:3050,y:600,w:500,d:800,rot:0,kind:"sink",host:"F-002"}),
      source: "t", status: "assumption" } },
    { row_id: "r7", version: 1, values: {
      element_id: "P-001", kind: "mep", name: "给水",
      payload: JSON.stringify({type:"water",x:3100,y:700}),
      source: "t", status: "confirmed" } },
    { row_id: "r8", version: 1, values: {
      element_id: "S-001", kind: "setting", name: "层高与口径",
      payload: JSON.stringify({ceiling_height:2600,thresholds:{"route.width_mm":800}}),
      source: "t", status: "confirmed" } }
  ],
  rooms: [
    { row_id: "r4", version: 1, values: {
      room_id: "R-001", name: "客厅", area_mm2: 12000000, source: "t",
      payload: JSON.stringify({polygon:[[0,0],[4000,0],[4000,3000],[0,3000]]}) } }
  ],
  diagnostics: [
    { row_id: "d0", version: 1, values: {
      rule_id: "ventilation.service_room", severity: "blocking", status: "not_verifiable",
      message: "旧诊断，应在重新校验时被替换", elements: "", acknowledged: false } }
  ],
  bom: []
};
window.octopApp = {
  listRows: function (options) {
    var table = (options || {}).table || "main";
    var stored = window.__rows[table] || [];
    return Promise.resolve({
      rows: stored.map(function (item) { return item.values; }),
      row_meta: stored.map(function (item) {
        return { row_id: item.row_id, version: item.version };
      })
    });
  },
  tables: function () {
    return Promise.resolve(Object.keys(window.__rows).map(function (key) {
      return { table_key: key };
    }));
  },
  appendRow: function (row, options) {
    var table = (options || {}).table || "main";
    var record = { row_id: "x" + (window.__appended.length + 1), version: 1, values: row };
    window.__appended.push({ table: table, row: row });
    (window.__rows[table] = window.__rows[table] || []).push(record);
    return Promise.resolve(record);
  },
  deleteRow: function (rowId, options) {
    var table = (options || {}).table || "main";
    window.__rows[table] = (window.__rows[table] || []).filter(function (item) {
      return item.row_id !== rowId;
    });
    return Promise.resolve({});
  },
  onDataChange: function () {},
  service: function () {
    return {
      invoke: function (tool, args) {
        window.__sent.push({ tool: tool, args: args });
        return Promise.resolve({
          status: "succeeded",
          result: {
            diagnostics: [{
              rule_id: "clearance.wardrobe_front",
              status: "non_compliant",
              severity: "blocking",
              message: "衣柜前方净空不足",
              elements: ["F-001"]
            }]
          }
        });
      }
    };
  }
};
</script>
"""


@pytest.fixture(scope="module")
def browser() -> Any:
    """A launched Chromium, or a skip when the host cannot run one."""
    path = _chromium_path()
    if not path:
        pytest.skip("no Chromium build available for the page regression test")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            instance = p.chromium.launch(executable_path=path)
        except Exception as exc:  # missing system libraries, sandbox denial, …
            pytest.skip(f"Chromium cannot launch here: {str(exc).splitlines()[0]}")
        try:
            yield instance
        finally:
            instance.close()


def test_page_sends_kind_and_highlights_the_finding(browser: Any) -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    page_html = html.replace("<script>", STUB_SDK + "<script>", 1)

    page = browser.new_page()
    try:
        page.set_content(page_html)
        page.wait_for_function("window.__sent.length === 0")
        page.click("#btnCheck")
        page.wait_for_function("window.__appended.length > 0")
        sent = page.evaluate("window.__sent")
        appended = page.evaluate("window.__appended")
        red = page.evaluate(
            "Array.from(document.querySelectorAll('#plan rect, #plan line'))"
            ".filter(function (n) { return n.getAttribute('stroke') === '#dc2626'; }).length"
        )
        # The stale row must be replaced, not appended next to: the page has to
        # read row ids from ``row_meta``, or the delete silently no-ops.
        remaining = page.evaluate(
            "window.__rows.diagnostics.map(function (r) { return r.values.rule_id; })"
        )
    finally:
        page.close()

    assert len(sent) == 1, sent
    ir = sent[0]["args"]["ir"]

    # The regression: the IR builder must forward every field the kernel reads.
    furniture = {item["id"]: item for item in ir["furniture"]}
    assert furniture["F-001"]["kind"] == "wardrobe"
    openings = {item["id"]: item for item in ir["openings"]}
    assert openings["D-001"]["host"] == "W-001"
    assert ir["rooms"][0]["polygon"][0] == [0, 0]
    # Hosted items keep their host, so the kernel allows the intended overlap.
    furniture_by_id = {item["id"]: item for item in ir["furniture"]}
    assert furniture_by_id["F-003"]["host"] == "F-002"
    # MEP points travel with the IR too.
    assert ir["mep"] == [{"id": "P-001", "type": "water", "x": 3100, "y": 700, "height": 0}]
    # A setting row carries the ceiling and the per-plan thresholds.
    assert ir["ceiling_height"] == 2600
    assert ir["thresholds"] == {"route.width_mm": 800}

    # The diagnostics come back and the page writes them into its table.
    diagnostics = [item for item in appended if item["table"] == "diagnostics"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["row"]["rule_id"] == "clearance.wardrobe_front"
    assert diagnostics[0]["row"]["elements"] == "F-001"

    # A blocking finding colours the element it points at.
    assert red >= 1

    # The stale row was replaced rather than duplicated.
    assert remaining == ["clearance.wardrobe_front"], remaining
