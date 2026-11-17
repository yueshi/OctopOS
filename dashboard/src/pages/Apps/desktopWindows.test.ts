import { describe, expect, it } from "vitest";

import {
  DOCK_SPACE,
  MENU_BAR_HEIGHT,
  WINDOW_REGISTRY,
  WINDOWS_STORE_KEY,
  browserWindowId,
  createDesktopWindow,
  filesWindowId,
  htmlWindowId,
  loadDesktopWindows,
  saveDesktopWindows,
  systemWindowId,
  type DesktopWindowState,
} from "./desktopWindows";

const appSpace = new Map([["app_1", "agent_1"]]);

function create(kind: "app" | "chat" | "external", bottomInset?: number) {
  return createDesktopWindow({
    kind,
    appId: kind === "app" ? "app_1" : undefined,
    agentId: kind === "chat" ? "agent_1" : undefined,
    spaceId: kind === "external" ? "agent_1" : undefined,
    url: kind === "external" ? "https://example.com" : undefined,
    z: 1,
    list: [],
    appSpace,
    viewportWidth: 1280,
    viewportHeight: 800,
    bottomInset,
  })!;
}

describe("desktopWindows", () => {
  it("keeps every window kind above the dock surface", () => {
    const dockTop = 800 - DOCK_SPACE;
    for (const kind of ["app", "chat", "external"] as const) {
      const win = create(kind);
      expect(win.y + win.h).toBeLessThanOrEqual(dockTop);
    }
  });

  it("uses the measured dock inset for placement", () => {
    const win = create("chat", 140);
    expect(win.y + win.h).toBeLessThanOrEqual(800 - 140);
    expect(win.y).toBeGreaterThanOrEqual(MENU_BAR_HEIGHT);
  });

  it("shrinks a tall window instead of letting it cross the dock", () => {
    const win = create("external", 200);
    expect(win.h).toBeLessThanOrEqual(800 - MENU_BAR_HEIGHT - 200);
    expect(win.y + win.h).toBeLessThanOrEqual(800 - 200);
  });

  it("creates one files window per expert above the dock", () => {
    const win = createDesktopWindow({
      kind: "files",
      agentId: "agent_1",
      z: 1,
      list: [],
      appSpace,
      viewportWidth: 1280,
      viewportHeight: 800,
    })!;
    expect(win.id).toBe(filesWindowId("agent_1"));
    expect(win.w).toBeGreaterThanOrEqual(WINDOW_REGISTRY.files.minWidth);
    expect(win.h).toBeGreaterThanOrEqual(WINDOW_REGISTRY.files.minHeight);
    expect(win.y + win.h).toBeLessThanOrEqual(800 - DOCK_SPACE);
  });

  it("creates one html window per file and keeps it above the dock", () => {
    const win = createDesktopWindow({
      kind: "html",
      agentId: "agent_1",
      path: "outbound/report.html",
      pathFromWorkspace: true,
      z: 1,
      list: [],
      appSpace,
      viewportWidth: 1280,
      viewportHeight: 800,
    })!;
    expect(win.id).toBe(htmlWindowId("agent_1", "outbound/report.html"));
    expect(win.path).toBe("outbound/report.html");
    expect(win.pathFromWorkspace).toBe(true);
    expect(win.w).toBeGreaterThanOrEqual(WINDOW_REGISTRY.html.minWidth);
    expect(win.y + win.h).toBeLessThanOrEqual(800 - DOCK_SPACE);
  });

  it("creates one blank browser window per expert", () => {
    const win = createDesktopWindow({
      kind: "browser",
      agentId: "agent_1",
      z: 1,
      list: [],
      appSpace,
      viewportWidth: 1280,
      viewportHeight: 800,
    })!;
    expect(win.id).toBe(browserWindowId("agent_1"));
    expect(win.url).toBeUndefined();
    expect(win.w).toBeGreaterThanOrEqual(WINDOW_REGISTRY.browser.minWidth);
    expect(win.y + win.h).toBeLessThanOrEqual(800 - DOCK_SPACE);
  });

  it("creates and restores one system window per app key", () => {
    localStorage.clear();
    const win = createDesktopWindow({
      kind: "system",
      systemKey: "usage",
      agentId: "agent_1",
      spaceId: "agent_1",
      title: "Usage",
      z: 1,
      list: [],
      appSpace,
      viewportWidth: 1280,
      viewportHeight: 800,
    })!;
    expect(win.id).toBe(systemWindowId("agent_1", "usage"));
    expect(win.systemKey).toBe("usage");
    expect(win.agentId).toBe("agent_1");
    expect(win.w).toBeGreaterThanOrEqual(WINDOW_REGISTRY.system.minWidth);
    expect(win.y + win.h).toBeLessThanOrEqual(800 - DOCK_SPACE);

    saveDesktopWindows([win]);
    const [loaded] = loadDesktopWindows();
    expect(loaded).toMatchObject({
      id: "system:agent_1:usage",
      kind: "system",
      systemKey: "usage",
      agentId: "agent_1",
      title: "Usage",
    });
    localStorage.clear();
  });

  it("round-trips the last browser URL", () => {
    localStorage.clear();
    const saved: DesktopWindowState = {
      id: "browser:agent_1",
      kind: "browser",
      agentId: "agent_1",
      url: "https://example.com/",
      x: 20,
      y: 60,
      w: 800,
      h: 600,
      z: 3,
      minimized: false,
      maximized: false,
    };
    saveDesktopWindows([saved]);
    const [loaded] = loadDesktopWindows();
    expect(loaded).toMatchObject({
      kind: "browser",
      agentId: "agent_1",
      url: "https://example.com/",
    });
    localStorage.clear();
  });

  it("round-trips files window state", () => {
    localStorage.clear();
    const base = createDesktopWindow({
      kind: "files",
      agentId: "agent_1",
      z: 4,
      list: [],
      appSpace,
      viewportWidth: 1280,
      viewportHeight: 800,
    })!;
    const saved: DesktopWindowState = {
      ...base,
      path: "outbound/report.html",
      threadId: "thr_1",
      pathFromWorkspace: true,
      filesTab: "all",
    };
    saveDesktopWindows([saved]);
    const [loaded] = loadDesktopWindows();
    expect(loaded).toMatchObject({
      id: "files:agent_1",
      kind: "files",
      agentId: "agent_1",
      path: "outbound/report.html",
      threadId: "thr_1",
      pathFromWorkspace: true,
      filesTab: "all",
    });
    localStorage.clear();
  });

  it("round-trips a standalone html window", () => {
    localStorage.clear();
    const saved: DesktopWindowState = {
      id: "html:agent_1:outbound%2Freport.html",
      kind: "html",
      agentId: "agent_1",
      path: "outbound/report.html",
      pathFromWorkspace: true,
      title: "report.html",
      x: 20,
      y: 60,
      w: 800,
      h: 600,
      z: 3,
      minimized: false,
      maximized: false,
    };
    saveDesktopWindows([saved]);
    const [loaded] = loadDesktopWindows();
    expect(loaded).toMatchObject({
      kind: "html",
      agentId: "agent_1",
      path: "outbound/report.html",
      pathFromWorkspace: true,
      title: "report.html",
    });
    localStorage.clear();
  });

  it("drops persisted windows with an unknown kind", () => {
    localStorage.setItem(
      WINDOWS_STORE_KEY,
      JSON.stringify([{ id: "x", kind: "bogus", agentId: "agent_1" }]),
    );
    expect(loadDesktopWindows()).toEqual([]);
    localStorage.clear();
  });
});
