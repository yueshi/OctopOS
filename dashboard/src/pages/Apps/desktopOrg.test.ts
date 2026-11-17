import { describe, expect, it } from "vitest";

import type { AppSummary } from "../../api/modules/apps";
import type { SystemAppDefinition } from "./system/types";
import {
  EMPTY_DESKTOP_ORG,
  appEntryId,
  createDesktopFolder,
  deleteDesktopFolder,
  dockAppsFor,
  folderEntryId,
  layoutDesktopEntries,
  reorderDesktopEntries,
  sanitizeDesktopOrg,
  setAppHidden,
  setAppPinned,
  setSystemAppHidden,
  updateDesktopFolder,
  visibleAppsFor,
  visibleSystemAppsFor,
} from "./desktopOrg";

function app(appId: string): AppSummary {
  return {
    app_id: appId,
    name: appId,
    description: "",
    agent_id: "agent_1",
    agent_name: "Expert",
    owner_user_id: 1,
    owner_username: "owner",
    permission: "edit_structure",
    row_count: 0,
    html_bytes: 0,
    data_bytes: 0,
    has_html: true,
    has_preview: false,
    created_at: 0,
    updated_at: 0,
  };
}

function systemApp(key: SystemAppDefinition["key"]): SystemAppDefinition {
  return {
    key,
    titleKey: key,
    descriptionKey: key,
    icon: () => null,
    permissions: [],
    queryKeys: [],
    loadHtml: async () => "",
  };
}

describe("desktop organization preferences", () => {
  it("sanitizes malformed storage and keeps explicit empty pins", () => {
    expect(
      sanitizeDesktopOrg({
        hiddenSystem: { agent_1: ["usage", "usage", 3], "": ["x"] },
        hiddenApps: ["a", null, "a"],
        pinnedApps: [],
      }),
    ).toEqual({
      hiddenSystem: { agent_1: ["usage"] },
      hiddenApps: ["a"],
      pinnedApps: [],
    });
  });

  it("hides system apps per expert desktop", () => {
    const apps = [systemApp("usage"), systemApp("tasks")];
    const prefs = setSystemAppHidden(
      EMPTY_DESKTOP_ORG,
      "agent_1",
      "usage",
      true,
    );
    expect(
      visibleSystemAppsFor(prefs, "agent_1", apps).map((a) => a.key),
    ).toEqual(["tasks"]);
    expect(visibleSystemAppsFor(prefs, "agent_2", apps)).toHaveLength(2);
  });

  it("filters hidden user apps and unpins them", () => {
    const apps = [app("a"), app("b")];
    const pinned = setAppPinned(EMPTY_DESKTOP_ORG, "b", true, ["a", "b"]);
    const hidden = setAppHidden(pinned, "b", true);
    expect(visibleAppsFor(hidden, apps).map((a) => a.app_id)).toEqual(["a"]);
    expect(hidden.pinnedApps).toEqual(["a"]);
  });

  it("uses the legacy first-N dock until the user pins an app", () => {
    const apps = [app("a"), app("b"), app("c")];
    expect(
      dockAppsFor(EMPTY_DESKTOP_ORG, apps, 2).map((a) => a.app_id),
    ).toEqual(["a", "b"]);

    const pinned = setAppPinned(EMPTY_DESKTOP_ORG, "c", true, ["a", "b"]);
    expect(dockAppsFor(pinned, apps, 10).map((a) => a.app_id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("keeps pinned order and ignores unknown or hidden ids", () => {
    const apps = [app("a"), app("b")];
    const prefs = {
      hiddenSystem: {},
      hiddenApps: ["b"],
      pinnedApps: ["b", "missing", "a"],
    };
    expect(dockAppsFor(prefs, apps, 10).map((a) => a.app_id)).toEqual(["a"]);
  });

  it("sanitizes folders, dropping duplicates and empty groups", () => {
    expect(
      sanitizeDesktopOrg({
        folders: [
          { id: "f1", name: "Sales", appIds: ["a", "a", "b"] },
          { id: "f1", name: "Duplicate", appIds: ["c"] },
          { id: "f2", name: "Taken", appIds: ["b"] },
          { id: "f3", name: "Empty", appIds: [] },
        ],
        order: ["folder:f1", "app:a", "folder:f1"],
      }),
    ).toEqual({
      hiddenSystem: {},
      hiddenApps: [],
      folders: [{ id: "f1", name: "Sales", appIds: ["a", "b"] }],
      order: ["folder:f1", "app:a"],
    });
  });

  it("renders folders as one entry and respects a custom order", () => {
    const apps = [app("a"), app("b"), app("c")];
    const prefs = createDesktopFolder(
      {
        ...EMPTY_DESKTOP_ORG,
        order: [appEntryId("b"), appEntryId("a"), appEntryId("c")],
      },
      "Sales",
      ["a", "b"],
      [],
      "f1",
    );
    const entries = layoutDesktopEntries(prefs, apps);
    expect(entries.map((entry) => entry.kind)).toEqual(["app", "folder"]);
    expect(entries[0].kind === "app" && entries[0].app.app_id).toBe("c");
    expect(
      entries[1].kind === "folder" &&
        entries[1].apps.map((item) => item.app_id),
    ).toEqual(["a", "b"]);
  });

  it("moves an entry before the drop target", () => {
    const prefs = reorderDesktopEntries(
      EMPTY_DESKTOP_ORG,
      appEntryId("c"),
      appEntryId("a"),
      [appEntryId("a"), appEntryId("b"), appEntryId("c")],
    );
    expect(prefs.order).toEqual([
      appEntryId("c"),
      appEntryId("a"),
      appEntryId("b"),
    ]);
  });

  it("creates and deletes folders without losing the apps", () => {
    const apps = [app("a"), app("b"), app("c")];
    const created = createDesktopFolder(
      EMPTY_DESKTOP_ORG,
      " Sales ",
      ["a", "b"],
      [appEntryId("a"), appEntryId("b"), appEntryId("c")],
      "f1",
    );
    expect(created.folders).toEqual([
      { id: "f1", name: "Sales", appIds: ["a", "b"] },
    ]);
    expect(created.order).toEqual([appEntryId("c"), folderEntryId("f1")]);

    const deleted = deleteDesktopFolder(created, "f1");
    expect(deleted.folders).toBeUndefined();
    expect(deleted.order).toEqual([appEntryId("c")]);
    expect(
      layoutDesktopEntries(deleted, apps).map((entry) =>
        entry.kind === "app" ? entry.app.app_id : `folder:${entry.folder.id}`,
      ),
    ).toEqual(["c", "a", "b"]);
  });

  it("renames a folder and moves apps in or out", () => {
    const apps = [app("a"), app("b"), app("c")];
    const created = createDesktopFolder(
      EMPTY_DESKTOP_ORG,
      "Sales",
      ["a", "b"],
      [appEntryId("a"), appEntryId("b"), appEntryId("c")],
      "f1",
    );
    const updated = updateDesktopFolder(
      created,
      "f1",
      " Customers ",
      ["b", "c"],
      [appEntryId("c"), folderEntryId("f1")],
    );
    expect(updated.folders).toEqual([
      { id: "f1", name: "Customers", appIds: ["b", "c"] },
    ]);
    expect(updated.order).toEqual([folderEntryId("f1")]);
    expect(
      layoutDesktopEntries(updated, apps).map((entry) =>
        entry.kind === "app" ? entry.app.app_id : `folder:${entry.folder.id}`,
      ),
    ).toEqual(["folder:f1", "a"]);
  });

  it("removes a hidden app from its folder", () => {
    const apps = [app("a"), app("b"), app("c")];
    const created = createDesktopFolder(
      EMPTY_DESKTOP_ORG,
      "Sales",
      ["a", "b"],
      [appEntryId("a"), appEntryId("b"), appEntryId("c")],
      "f1",
    );
    const hidden = setAppHidden(created, "a", true);
    expect(hidden.folders).toEqual([
      { id: "f1", name: "Sales", appIds: ["b"] },
    ]);
    const entries = layoutDesktopEntries(hidden, apps);
    expect(entries).toHaveLength(2);
    const folder = entries.find((entry) => entry.kind === "folder");
    expect(
      folder?.kind === "folder" && folder.apps.map((item) => item.app_id),
    ).toEqual(["b"]);
  });
});
