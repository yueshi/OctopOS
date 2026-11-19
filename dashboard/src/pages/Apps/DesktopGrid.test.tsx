import type { ReactElement } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppSummary } from "../../api/modules/apps";
import DesktopGrid, {
  LIVE_IFRAME_BUDGET,
  MINIMIZED_WINDOW_SUSPEND_MS,
  iconSpecForApp,
} from "./DesktopGrid";

function renderGrid(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
    i18n: { language: "en" },
  }),
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../components/MiniAppFrame", () => ({
  default: ({
    appId,
    onOpenExternal,
  }: {
    appId: string;
    onOpenExternal?: (url: string) => void;
  }) => (
    <div>
      <div data-testid="mini-app-frame" data-app-id={appId} />
      <button
        type="button"
        data-testid="mini-app-open-external"
        onClick={() => onOpenExternal?.("https://example.com/path")}
      />
    </div>
  ),
}));

vi.mock("./DesktopWebView", () => ({
  default: ({ url }: { url: string }) => (
    <div data-testid="desktop-webview" data-url={url} />
  ),
}));

vi.mock("./DesktopFilesWindow", () => ({
  default: ({
    agentId,
    onOpenHtml,
  }: {
    agentId: string;
    onOpenHtml?: (file: {
      agentId: string;
      path: string;
      name: string;
      fromWorkspace: boolean;
    }) => void;
  }) => (
    <div data-testid="desktop-files-window" data-agent-id={agentId}>
      <button
        type="button"
        data-testid="mock-open-html"
        onClick={() =>
          onOpenHtml?.({
            agentId,
            path: "outbound/report.html",
            name: "report.html",
            fromWorkspace: true,
          })
        }
      />
    </div>
  ),
}));

vi.mock("./DesktopBrowserWindow", () => ({
  default: ({
    windowId,
    url,
    onNavigate,
  }: {
    windowId: string;
    url: string;
    onNavigate: (windowId: string, url: string) => void;
  }) => (
    <div data-testid="desktop-browser-window" data-url={url}>
      <button
        type="button"
        data-testid="mock-browser-navigate"
        onClick={() => onNavigate(windowId, "https://example.com/")}
      />
    </div>
  ),
}));

vi.mock("./DesktopHtmlWindow", () => ({
  default: ({
    agentId,
    path,
    fromWorkspace,
  }: {
    agentId: string;
    path: string;
    fromWorkspace: boolean;
  }) => (
    <div
      data-testid="desktop-html-window"
      data-agent-id={agentId}
      data-path={path}
      data-from-workspace={fromWorkspace ? "true" : "false"}
    />
  ),
}));

vi.mock("./DesktopChatWindow", () => ({
  default: ({
    agentId,
    initialAppId,
    seed,
    onOpenFile,
  }: {
    agentId: string;
    initialAppId?: string | null;
    seed?: { id: number; text: string } | null;
    onOpenFile?: (
      path: string,
      agentId: string | null,
      threadId: string | null,
    ) => void;
  }) => (
    <div
      data-testid="desktop-chat"
      data-agent-id={agentId}
      data-app-id={initialAppId ?? ""}
      data-seed={seed?.text ?? ""}
    >
      <button
        type="button"
        data-testid="mock-chat-file"
        onClick={() => onOpenFile?.("outbound/report.html", "agent_1", "thr_1")}
      />
      <button
        type="button"
        data-testid="mock-chat-root-file"
        onClick={() => onOpenFile?.("report.html", "agent_1", "thr_1")}
      />
    </div>
  ),
}));

function openAppWindow(appId: string) {
  const icon = screen
    .getAllByTestId("desktop-icon")
    .find((node) => node.getAttribute("data-app-id") === appId)!;
  fireEvent.doubleClick(icon);
}

function switchSpace(spaceId: string) {
  if (!screen.queryByTestId("desktop-spaces-menu")) {
    fireEvent.click(screen.getByTestId("desktop-space-trigger"));
  }
  const option = screen
    .getAllByTestId("desktop-space-option")
    .find((node) => node.getAttribute("data-space-id") === spaceId)!;
  fireEvent.click(option);
}

const base: AppSummary = {
  app_id: "app_1",
  name: "客户跟进台账",
  description: "按阶段管理客户",
  agent_id: "agent_1",
  agent_name: "销售助理",
  owner_user_id: 1,
  owner_username: "boss",
  permission: "edit_structure",
  row_count: 12,
  html_bytes: 100,
  data_bytes: 100,
  has_html: true,
  has_preview: false,
  created_at: 0,
  updated_at: 0,
};

const apps = [base, { ...base, app_id: "app_2", name: "本月业绩看板" }];

describe("DesktopGrid", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders every app as a desktop icon", () => {
    renderGrid(<DesktopGrid apps={apps} />);

    expect(screen.getByTestId("desktop-root")).toBeInTheDocument();
    expect(screen.getByTestId("desktop-dock")).toBeInTheDocument();
    expect(screen.getAllByTestId("desktop-icon")).toHaveLength(2);
    expect(screen.getByText("客户跟进台账")).toBeInTheDocument();
    expect(screen.getByText("本月业绩看板")).toBeInTheDocument();
  });

  it("reorders desktop icons by drag and drop", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    const icons = () =>
      screen
        .getAllByTestId("desktop-icon")
        .map((node) => node.getAttribute("data-app-id"));
    const [first, second] = icons();
    const store = new Map<string, string>();
    const dataTransfer = {
      effectAllowed: "none",
      dropEffect: "none",
      setData: (type: string, value: string) => store.set(type, value),
      getData: (type: string) => store.get(type) ?? "",
    };
    const nodes = screen.getAllByTestId("desktop-icon");

    fireEvent.dragStart(nodes[1], { dataTransfer });
    fireEvent.dragOver(nodes[0], { dataTransfer });
    fireEvent.drop(nodes[0], { dataTransfer });

    expect(icons()).toEqual([second, first]);
  });

  it("renders a custom folder as one icon and opens its apps", () => {
    localStorage.setItem(
      "octop:apps-desktop-org:v1",
      JSON.stringify({
        hiddenSystem: {},
        hiddenApps: [],
        folders: [{ id: "f1", name: "客户", appIds: ["app_1"] }],
        order: ["folder:f1", "app:app_2"],
      }),
    );
    renderGrid(<DesktopGrid apps={apps} />);

    expect(screen.getByTestId("desktop-app-folder")).toHaveTextContent("客户");
    expect(screen.getAllByTestId("desktop-icon")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("desktop-app-folder"));
    expect(screen.getByTestId("desktop-app-folder-item")).toHaveAttribute(
      "data-app-id",
      "app_1",
    );
  });

  it("reveals the dock from the bottom hotspot and hides it after leaving", async () => {
    renderGrid(<DesktopGrid apps={apps} />);
    const dock = screen.getByTestId("desktop-dock");
    expect(dock).toHaveAttribute("data-revealed", "false");

    fireEvent.pointerEnter(screen.getByTestId("desktop-dock-hotspot"));
    expect(dock).toHaveAttribute("data-revealed", "true");

    fireEvent.pointerLeave(dock);
    await waitFor(
      () => expect(dock).toHaveAttribute("data-revealed", "false"),
      { timeout: 1200 },
    );
  });

  it("draws generated icons instead of app screenshots", () => {
    const { container } = renderGrid(<DesktopGrid apps={apps} />);

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelectorAll("svg").length).toBeGreaterThan(0);
  });

  it("derives a stable icon spec per app", () => {
    const spec = iconSpecForApp(apps[0]);

    expect(iconSpecForApp(apps[0])).toEqual(spec);
    expect(spec.from).toMatch(/^#[0-9a-f]{6}$/i);
    expect(spec.to).toMatch(/^#[0-9a-f]{6}$/i);
    expect(["aurora", "glass", "ring", "mesh", "solid"]).toContain(spec.style);
    expect(spec.Glyph).toBeTruthy();
  });

  it("varies generated icons for generic app names", () => {
    const generic = Array.from({ length: 8 }, (_, index) => ({
      ...base,
      app_id: `app_generic_${index}`,
      name: `事项 ${index}`,
      description: "",
    }));
    const keys = new Set(
      generic.map((app) => {
        const spec = iconSpecForApp(app);
        return `${spec.from}|${spec.to}|${spec.style}|${spec.Glyph}`;
      }),
    );

    expect(keys.size).toBeGreaterThan(4);
  });

  it("plays the Agent OS boot before entering fullscreen from the menu bar", () => {
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });

    renderGrid(<DesktopGrid apps={apps} />);
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));

    expect(requestFullscreen).toHaveBeenCalled();
    expect(screen.getByTestId("desktop-boot")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(screen.queryByTestId("desktop-boot")).not.toBeInTheDocument();
  });

  it("marks fullscreen so the desktop can drop expensive glass blur", () => {
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value: vi.fn().mockResolvedValue(undefined),
    });
    renderGrid(<DesktopGrid apps={apps} />);
    const root = screen.getByTestId("desktop-root");
    expect(root).toHaveAttribute("data-fullscreen", "false");

    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      value: root,
    });
    fireEvent(document, new Event("fullscreenchange"));
    expect(root).toHaveAttribute("data-fullscreen", "true");

    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      value: null,
    });
    fireEvent(document, new Event("fullscreenchange"));
    expect(root).toHaveAttribute("data-fullscreen", "false");
  });

  it("auto-enters fullscreen and plays the 4.5-second 智管家 OS boot", () => {
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });
    vi.useFakeTimers();
    try {
      renderGrid(<DesktopGrid apps={apps} autoFullscreen />);

      expect(screen.getByTestId("desktop-boot")).toBeInTheDocument();
      expect(screen.getByText("智管家")).toBeInTheDocument();
      expect(requestFullscreen).toHaveBeenCalledTimes(1);

      act(() => {
        vi.advanceTimersByTime(4500);
      });
      expect(screen.queryByTestId("desktop-boot")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("lets the user skip the Agent OS boot sequence", () => {
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value: vi.fn().mockResolvedValue(undefined),
    });
    renderGrid(<DesktopGrid apps={apps} autoFullscreen />);

    fireEvent.click(screen.getByRole("button", { name: "Skip" }));

    expect(screen.queryByTestId("desktop-boot")).not.toBeInTheDocument();
  });

  it("opens an app in a desktop window on double-click", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-app-id",
      "app_1",
    );
    expect(screen.getByTestId("mini-app-frame")).toHaveAttribute(
      "data-app-id",
      "app_1",
    );
    expect(screen.getByTestId("dock-running")).toBeInTheDocument();
  });

  it("raises a background app window when its iframe area is clicked", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");
    openAppWindow("app_2");

    const windowNode = (appId: string) =>
      screen
        .getAllByTestId("desktop-window")
        .find((node) => node.getAttribute("data-app-id") === appId)!;

    const first = windowNode("app_1");
    const second = windowNode("app_2");
    expect(
      second.querySelector('[data-testid="desktop-window-focus-shield"]'),
    ).toBeNull();

    const shield = first.querySelector(
      '[data-testid="desktop-window-focus-shield"]',
    );
    expect(shield).not.toBeNull();
    fireEvent(
      shield!,
      new MouseEvent("pointerdown", { bubbles: true, button: 0 }),
    );

    expect(Number(first.style.zIndex)).toBeGreaterThan(
      Number(second.style.zIndex),
    );
    expect(
      first.querySelector('[data-testid="desktop-window-focus-shield"]'),
    ).toBeNull();
  });

  it("keeps at most the iframe budget live per desktop", () => {
    const budgetApps: AppSummary[] = Array.from(
      { length: LIVE_IFRAME_BUDGET + 1 },
      (_, index) => ({
        ...base,
        app_id: `app_budget_${index}`,
        name: `预算应用 ${index}`,
      }),
    );
    renderGrid(<DesktopGrid apps={budgetApps} />);

    for (const item of budgetApps) openAppWindow(item.app_id);

    const windowNode = (appId: string) =>
      screen
        .getAllByTestId("desktop-window")
        .find((node) => node.getAttribute("data-app-id") === appId)!;
    const sleepingIds = () =>
      screen
        .getAllByTestId("desktop-window")
        .filter((node) => node.getAttribute("data-suspended") === "true")
        .map((node) => node.getAttribute("data-app-id"));

    expect(screen.getAllByTestId("mini-app-frame")).toHaveLength(
      LIVE_IFRAME_BUDGET,
    );
    expect(sleepingIds()).toEqual(["app_budget_0"]);
    // The last opened window owns focus and must stay live.
    expect(
      windowNode(`app_budget_${LIVE_IFRAME_BUDGET}`).querySelector(
        '[data-testid="mini-app-frame"]',
      ),
    ).not.toBeNull();

    // Raising the sleeping window wakes it and evicts the next oldest one.
    const shield = windowNode("app_budget_0").querySelector(
      '[data-testid="desktop-window-focus-shield"]',
    );
    fireEvent(
      shield!,
      new MouseEvent("pointerdown", { bubbles: true, button: 0 }),
    );

    expect(screen.getAllByTestId("mini-app-frame")).toHaveLength(
      LIVE_IFRAME_BUDGET,
    );
    expect(sleepingIds()).toEqual(["app_budget_1"]);
    expect(
      windowNode("app_budget_0").querySelector(
        '[data-testid="mini-app-frame"]',
      ),
    ).not.toBeNull();
  });

  it("opens an app window with Enter for keyboard users", () => {
    renderGrid(<DesktopGrid apps={apps} />);

    const icon = screen
      .getAllByTestId("desktop-icon")
      .find((node) => node.getAttribute("data-app-id") === "app_2")!;
    fireEvent.keyDown(icon, { key: "Enter" });

    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-app-id",
      "app_2",
    );
  });

  it("moves a window through a compositor transform while dragging", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");
    const root = screen.getByTestId("desktop-root");
    Object.defineProperty(root, "clientWidth", {
      configurable: true,
      value: 1280,
    });
    Object.defineProperty(root, "clientHeight", {
      configurable: true,
      value: 800,
    });

    const win = screen.getByTestId("desktop-window");
    const header = document.querySelector("[data-titlebar]")!;
    // jsdom has no PointerEvent constructor; MouseEvent with the pointerdown
    // type still reaches React's onPointerDown and native pointer listeners.
    fireEvent(
      header,
      new MouseEvent("pointerdown", {
        bubbles: true,
        button: 0,
        clientX: 100,
        clientY: 100,
      }),
    );
    fireEvent(
      window,
      new MouseEvent("pointermove", {
        bubbles: true,
        clientX: 160,
        clientY: 140,
      }),
    );

    expect(win).toHaveAttribute("data-dragging", "true");
    expect(win.style.transform).toContain("translate3d(60px, 40px");
    expect(win.style.left).toBe("64px");

    fireEvent(window, new MouseEvent("pointerup", { bubbles: true }));
    expect(win).toHaveAttribute("data-dragging", "false");
    expect(win.style.transform).toBe("");
    expect(win.style.left).toBe("124px");
    expect(win.style.top).toBe("96px");
  });

  it("scales window content while resizing and commits once", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");
    const root = screen.getByTestId("desktop-root");
    Object.defineProperty(root, "clientWidth", {
      configurable: true,
      value: 1280,
    });
    Object.defineProperty(root, "clientHeight", {
      configurable: true,
      value: 800,
    });

    const win = screen.getByTestId("desktop-window");
    const content = screen.getByTestId("desktop-window-content");
    const handle = screen.getByTestId("desktop-window-resize");
    fireEvent(
      handle,
      new MouseEvent("pointerdown", {
        bubbles: true,
        button: 0,
        clientX: 500,
        clientY: 500,
      }),
    );
    fireEvent(
      window,
      new MouseEvent("pointermove", {
        bubbles: true,
        clientX: 580,
        clientY: 560,
      }),
    );

    expect(win).toHaveAttribute("data-resizing", "true");
    expect(win.style.width).toBe("840px");
    expect(win.style.height).toBe("620px");
    expect(content.style.width).toBe("758px");
    expect(content.style.transform).toContain("scale(");

    fireEvent(window, new MouseEvent("pointerup", { bubbles: true }));
    expect(win).toHaveAttribute("data-resizing", "false");
    expect(win.style.width).toBe("840px");
    expect(content.style.transform).toBe("");
  });

  it("resizes from the west and north edges while keeping opposite edges fixed", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");
    const root = screen.getByTestId("desktop-root");
    Object.defineProperty(root, "clientWidth", {
      configurable: true,
      value: 1280,
    });
    Object.defineProperty(root, "clientHeight", {
      configurable: true,
      value: 800,
    });

    for (const dir of ["n", "s", "e", "w", "ne", "nw", "se", "sw"]) {
      const testId =
        dir === "se" ? "desktop-window-resize" : `desktop-window-resize-${dir}`;
      expect(screen.getByTestId(testId)).toBeInTheDocument();
    }

    const win = screen.getByTestId("desktop-window");
    const left = Number.parseFloat(win.style.left);
    const top = Number.parseFloat(win.style.top);
    const width = Number.parseFloat(win.style.width);
    const height = Number.parseFloat(win.style.height);

    const nw = screen.getByTestId("desktop-window-resize-nw");
    fireEvent(
      nw,
      new MouseEvent("pointerdown", {
        bubbles: true,
        button: 0,
        clientX: 200,
        clientY: 200,
      }),
    );
    fireEvent(
      window,
      new MouseEvent("pointermove", {
        bubbles: true,
        clientX: 240,
        clientY: 230,
      }),
    );

    expect(win).toHaveAttribute("data-resizing", "true");
    expect(win.style.left).toBe(`${left + 40}px`);
    expect(win.style.top).toBe(`${top + 30}px`);
    expect(win.style.width).toBe(`${width - 40}px`);
    expect(win.style.height).toBe(`${height - 30}px`);

    fireEvent(window, new MouseEvent("pointerup", { bubbles: true }));
    expect(win).toHaveAttribute("data-resizing", "false");
    expect(win.style.left).toBe(`${left + 40}px`);
    expect(win.style.top).toBe(`${top + 30}px`);
    expect(win.style.width).toBe(`${width - 40}px`);
    expect(win.style.height).toBe(`${height - 30}px`);
  });

  it("reveals the window title bar only from the top edge", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    const zone = screen.getByTestId("desktop-window-top-zone");
    const header = document.querySelector("[data-titlebar]")!;
    expect(header).toHaveAttribute("data-titlebar", "false");

    fireEvent.pointerEnter(zone);
    expect(header).toHaveAttribute("data-titlebar", "true");

    vi.useFakeTimers();
    try {
      fireEvent.pointerLeave(header);
      act(() => {
        vi.advanceTimersByTime(220);
      });
      expect(header).toHaveAttribute("data-titlebar", "false");
    } finally {
      vi.useRealTimers();
    }
  });

  it("unloads a minimized app window quickly and reloads it", () => {
    vi.useFakeTimers();
    try {
      renderGrid(<DesktopGrid apps={apps} />);
      openAppWindow("app_1");
      expect(screen.getByTestId("mini-app-frame")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Minimize" }));
      expect(screen.getByTestId("mini-app-frame")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(MINIMIZED_WINDOW_SUSPEND_MS);
      });
      expect(screen.queryByTestId("mini-app-frame")).not.toBeInTheDocument();
      expect(
        screen.getByTestId("desktop-window-suspended"),
      ).toBeInTheDocument();

      const dockIcon = screen
        .getAllByTestId("desktop-dock-icon")
        .find((node) => node.getAttribute("data-app-id") === "app_1")!;
      fireEvent.click(dockIcon);
      expect(screen.getByTestId("mini-app-frame")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("unloads a minimized browser window quickly", async () => {
    renderGrid(<DesktopGrid apps={apps} />);
    switchSpace("agent_1");
    fireEvent.click(screen.getByTestId("desktop-dock-browser"));
    expect(
      await screen.findByTestId("desktop-browser-window"),
    ).toBeInTheDocument();

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("button", { name: "Minimize" }));
      act(() => {
        vi.advanceTimersByTime(MINIMIZED_WINDOW_SUSPEND_MS);
      });

      expect(
        screen.queryByTestId("desktop-browser-window"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByTestId("desktop-window-suspended"),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("minimizes, maximizes/restores and closes a window", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    const windowNode = () => screen.getByTestId("desktop-window");

    fireEvent.click(screen.getByRole("button", { name: "Minimize" }));
    expect(windowNode()).toHaveAttribute("data-minimized", "true");

    const dockIcon = screen
      .getAllByTestId("desktop-dock-icon")
      .find((node) => node.getAttribute("data-app-id") === "app_1")!;
    fireEvent.click(dockIcon);
    expect(windowNode()).toHaveAttribute("data-minimized", "false");

    fireEvent.click(screen.getByRole("button", { name: "Maximize" }));
    expect(windowNode()).toHaveAttribute("data-maximized", "true");

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(windowNode()).toHaveAttribute("data-maximized", "false");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("desktop-window")).not.toBeInTheDocument();
  });

  it("keeps a maximized window full when the dock is revealed", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    const root = screen.getByTestId("desktop-root");
    Object.defineProperty(root, "clientWidth", {
      configurable: true,
      value: 1280,
    });
    Object.defineProperty(root, "clientHeight", {
      configurable: true,
      value: 800,
    });
    openAppWindow("app_1");

    fireEvent.click(screen.getByRole("button", { name: "Maximize" }));
    const win = screen.getByTestId("desktop-window");
    expect(win.style.height).toBe("762px");

    fireEvent.pointerEnter(screen.getByTestId("desktop-dock-hotspot"));
    expect(screen.getByTestId("desktop-dock")).toHaveAttribute(
      "data-revealed",
      "true",
    );
    expect(win.style.height).toBe("762px");
  });

  it("groups apps into one desktop per expert and switches desktops", () => {
    const otherExpert = {
      ...base,
      app_id: "app_ops",
      name: "库存预警",
      agent_id: "agent_2",
      agent_name: "运营专家",
    };
    renderGrid(<DesktopGrid apps={[...apps, otherExpert]} />);

    fireEvent.click(screen.getByTestId("desktop-space-trigger"));
    // Two expert desktops plus the global All Apps library.
    expect(screen.getAllByTestId("desktop-space-option")).toHaveLength(3);
    expect(screen.getByText("All Apps")).toBeInTheDocument();
    expect(screen.getByTestId("desktop-spaces-menu")).toBeInTheDocument();

    switchSpace("agent_2");

    expect(screen.getAllByTestId("desktop-icon")).toHaveLength(1);
    expect(screen.getByText("库存预警")).toBeInTheDocument();
    expect(screen.queryByText("客户跟进台账")).not.toBeInTheDocument();
  });

  it("keeps windows scoped to their expert desktop", () => {
    const otherExpert = {
      ...base,
      app_id: "app_ops",
      name: "库存预警",
      agent_id: "agent_2",
      agent_name: "运营专家",
    };
    renderGrid(<DesktopGrid apps={[...apps, otherExpert]} />);
    switchSpace("agent_1");
    openAppWindow("app_1");
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-app-id",
      "app_1",
    );

    switchSpace("agent_2");
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-current",
      "false",
    );

    switchSpace("agent_1");
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-current",
      "true",
    );
  });

  it("opens the files window for the current expert", async () => {
    renderGrid(<DesktopGrid apps={apps} />);
    switchSpace("agent_1");

    const surface = screen.getByTestId("desktop-dock-surface");
    const slots = [...surface.children];
    expect(
      slots[0]?.querySelector('[data-testid="desktop-dock-files"]'),
    ).not.toBeNull();
    expect(
      slots[0]?.querySelector('[data-testid="desktop-dock-divider"]'),
    ).toBeNull();
    expect(
      slots[1]?.querySelector('[data-testid="desktop-dock-browser"]'),
    ).not.toBeNull();
    expect(
      slots[1]?.querySelector('[data-testid="desktop-dock-divider"]'),
    ).not.toBeNull();

    fireEvent.click(screen.getByTestId("desktop-dock-files"));

    expect(await screen.findByTestId("desktop-files-window")).toHaveAttribute(
      "data-agent-id",
      "agent_1",
    );
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-kind",
      "files",
    );
  });

  it("opens a blank browser window and persists navigation", async () => {
    renderGrid(<DesktopGrid apps={apps} />);
    switchSpace("agent_1");

    fireEvent.click(screen.getByTestId("desktop-dock-browser"));
    expect(await screen.findByTestId("desktop-browser-window")).toHaveAttribute(
      "data-url",
      "",
    );

    fireEvent.click(screen.getByTestId("mock-browser-navigate"));
    await waitFor(() =>
      expect(screen.getByTestId("desktop-browser-window")).toHaveAttribute(
        "data-url",
        "https://example.com/",
      ),
    );
    const browserWindow = screen
      .getAllByTestId("desktop-window")
      .find((node) => node.getAttribute("data-kind") === "browser");
    expect(browserWindow).toHaveAttribute("data-url", "https://example.com/");
  });

  it("opens an independent HTML window from the files window", async () => {
    renderGrid(<DesktopGrid apps={apps} />);
    switchSpace("agent_1");

    fireEvent.click(screen.getByTestId("desktop-dock-files"));
    fireEvent.click(await screen.findByTestId("mock-open-html"));

    expect(await screen.findByTestId("desktop-html-window")).toHaveAttribute(
      "data-path",
      "outbound/report.html",
    );
    const windows = screen.getAllByTestId("desktop-window");
    expect(
      windows.some((node) => node.getAttribute("data-kind") === "html"),
    ).toBe(true);
  });

  it("opens HTML file cards in an independent window", async () => {
    renderGrid(<DesktopGrid apps={apps} />);
    switchSpace("agent_1");

    fireEvent.click(screen.getByTestId("desktop-pet"));
    fireEvent.click(screen.getByTestId("desktop-pet"));
    fireEvent.click(await screen.findByTestId("mock-chat-file"));

    expect(await screen.findByTestId("desktop-html-window")).toHaveAttribute(
      "data-path",
      "/outbound/report.html",
    );
  });

  it("treats relative chat file paths as workspace files", async () => {
    renderGrid(<DesktopGrid apps={apps} />);
    switchSpace("agent_1");

    fireEvent.click(screen.getByTestId("desktop-pet"));
    fireEvent.click(screen.getByTestId("desktop-pet"));
    fireEvent.click(await screen.findByTestId("mock-chat-root-file"));

    expect(await screen.findByTestId("desktop-html-window")).toHaveAttribute(
      "data-path",
      "/report.html",
    );
    expect(screen.getByTestId("desktop-html-window")).toHaveAttribute(
      "data-from-workspace",
      "true",
    );
  });

  it("opens expert chat inside the desktop from the pet", () => {
    renderGrid(<DesktopGrid apps={apps} />);

    fireEvent.click(screen.getByTestId("desktop-pet"));
    fireEvent.click(screen.getByTestId("desktop-pet"));

    expect(screen.getAllByTestId("desktop-chat")).toHaveLength(1);
    expect(screen.getByTestId("desktop-chat")).toHaveAttribute(
      "data-agent-id",
      "agent_1",
    );
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-kind",
      "chat",
    );
  });

  it("switches desktops with Ctrl+Arrow", () => {
    const otherExpert = {
      ...base,
      app_id: "app_ops",
      name: "库存预警",
      agent_id: "agent_2",
      agent_name: "运营专家",
    };
    renderGrid(<DesktopGrid apps={[...apps, otherExpert]} />);

    const trigger = screen.getByTestId("desktop-space-trigger");
    const initial = trigger.textContent;
    fireEvent.keyDown(window, { key: "ArrowRight", ctrlKey: true });

    expect(trigger.textContent).not.toBe(initial);
  });

  it("opens the command palette and launches an app", async () => {
    renderGrid(<DesktopGrid apps={apps} />);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(
      await screen.findByTestId("desktop-command-palette"),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("desktop-command-input"), {
      target: { value: "本月" },
    });
    fireEvent.click(screen.getByTestId("desktop-command-item"));

    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-app-id",
      "app_2",
    );
    expect(
      screen.queryByTestId("desktop-command-palette"),
    ).not.toBeInTheDocument();
  });

  it("does not boot windows from another desktop until it is visited", async () => {
    const otherApp: AppSummary = {
      ...base,
      app_id: "app_ops",
      name: "库存预警",
      agent_id: "agent_2",
      agent_name: "运营专家",
    };
    localStorage.setItem(
      "octop:apps-desktop-windows:v1",
      JSON.stringify([
        {
          id: "app:app_ops",
          kind: "app",
          appId: "app_ops",
          agentId: "agent_2",
          z: 21,
          minimized: false,
          maximized: false,
          x: 40,
          y: 60,
          w: 520,
          h: 400,
        },
      ]),
    );
    localStorage.setItem("octop:apps-desktop-space:v1", "agent_1");

    renderGrid(<DesktopGrid apps={[...apps, otherApp]} />);

    expect(screen.queryByTestId("mini-app-frame")).not.toBeInTheDocument();
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-app-id",
      "app_ops",
    );
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-suspended",
      "true",
    );

    switchSpace("agent_2");

    expect(await screen.findByTestId("mini-app-frame")).toHaveAttribute(
      "data-app-id",
      "app_ops",
    );
  });

  it("restores open desktop windows after a reload", async () => {
    const first = renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    await waitFor(() =>
      expect(localStorage.getItem("octop:apps-desktop-windows:v1")).toContain(
        "app_1",
      ),
    );
    first.unmount();

    renderGrid(<DesktopGrid apps={apps} />);
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-app-id",
      "app_1",
    );
  });

  it("opens MiniApp external links in an in-desktop web window", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    fireEvent.click(screen.getByTestId("mini-app-open-external"));

    const external = screen
      .getAllByTestId("desktop-window")
      .find((node) => node.getAttribute("data-kind") === "external")!;
    expect(external).toHaveAttribute("data-url", "https://example.com/path");
    expect(external).toHaveAttribute("data-current", "true");
    expect(screen.getByTestId("desktop-webview")).toHaveAttribute(
      "data-url",
      "https://example.com/path",
    );
    expect(screen.getByText("example.com")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("mini-app-open-external"));
    expect(screen.getAllByTestId("desktop-webview")).toHaveLength(1);
  });

  it("opens each expert's own chat from that desktop's pet", () => {
    const otherExpert = {
      ...base,
      app_id: "app_ops",
      name: "库存预警",
      agent_id: "agent_2",
      agent_name: "运营专家",
    };
    renderGrid(<DesktopGrid apps={[...apps, otherExpert]} />);

    switchSpace("agent_2");
    fireEvent.click(screen.getByTestId("desktop-pet"));

    expect(screen.getByTestId("desktop-chat")).toHaveAttribute(
      "data-agent-id",
      "agent_2",
    );
  });

  it("seeds app-specific expert actions into the desktop chat window", () => {
    renderGrid(<DesktopGrid apps={apps} />);

    const icon = screen
      .getAllByTestId("desktop-icon")
      .find((node) => node.getAttribute("data-app-id") === "app_1")!;
    fireEvent.click(icon);

    expect(screen.getByTestId("desktop-app-actions")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("desktop-action-optimize"));

    const chat = screen.getByTestId("desktop-chat");
    expect(chat).toHaveAttribute("data-agent-id", "agent_1");
    expect(chat).toHaveAttribute("data-app-id", "app_1");
    expect(chat.getAttribute("data-seed")).toContain("客户跟进台账");
    expect(chat.getAttribute("data-seed")).toContain("app_1");
  });

  it("passes the selected app into expert chat from the pet", () => {
    renderGrid(<DesktopGrid apps={apps} />);

    const icon = screen
      .getAllByTestId("desktop-icon")
      .find((node) => node.getAttribute("data-app-id") === "app_1")!;
    fireEvent.click(icon);
    fireEvent.click(screen.getByTestId("desktop-pet"));

    const chat = screen.getByTestId("desktop-chat");
    expect(chat).toHaveAttribute("data-app-id", "app_1");
    expect(chat).toHaveAttribute("data-seed", "");
  });

  it("opens Mission Control from the menu bar and F3", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    fireEvent.click(screen.getByTestId("desktop-mission-button"));
    const mission = screen.getByTestId("desktop-mission-control");
    expect(mission).toBeInTheDocument();
    expect(screen.getAllByTestId("desktop-mission-space")).toHaveLength(2);
    expect(screen.getAllByTestId("desktop-mission-window")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("desktop-mission-window"));
    expect(
      screen.queryByTestId("desktop-mission-control"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("desktop-window")).toHaveAttribute(
      "data-minimized",
      "false",
    );

    fireEvent.keyDown(window, { key: "F3" });
    expect(screen.getByTestId("desktop-mission-control")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.queryByTestId("desktop-mission-control"),
    ).not.toBeInTheDocument();
  });

  it("shows chat, web and All Apps entries in Dock 2.0", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");
    fireEvent.click(screen.getByTestId("desktop-pet"));
    fireEvent.click(screen.getByTestId("mini-app-open-external"));

    expect(screen.getByTestId("desktop-dock-chat")).toBeInTheDocument();
    expect(screen.getByTestId("desktop-dock-browser")).toBeInTheDocument();
    expect(screen.getByTestId("desktop-dock-external")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("desktop-dock-launcher"));
    expect(screen.getAllByTestId("desktop-icon")).toHaveLength(2);
  });

  it("limits the dock to ten MiniApp entries", () => {
    const many = Array.from({ length: 12 }, (_, index) => ({
      ...base,
      app_id: `app_${index}`,
      name: `MiniApp ${index}`,
    }));
    renderGrid(<DesktopGrid apps={many} />);

    expect(screen.getAllByTestId("desktop-dock-icon")).toHaveLength(10);
    expect(screen.getAllByTestId("desktop-icon")).toHaveLength(12);
  });

  it("closes a running window from the dock context menu", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    const dockIcon = screen
      .getAllByTestId("desktop-dock-icon")
      .find((node) => node.getAttribute("data-app-id") === "app_1")!;
    fireEvent.contextMenu(dockIcon);
    expect(screen.getByTestId("desktop-dock-menu")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: "Close" }));
    expect(screen.queryByTestId("desktop-window")).not.toBeInTheDocument();
  });

  it("summarizes experts and windows in the Agent Activity Center", () => {
    renderGrid(<DesktopGrid apps={apps} />);
    openAppWindow("app_1");

    expect(screen.getByTestId("desktop-activity-badge")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("desktop-activity-button"));

    expect(screen.getByTestId("desktop-activity-panel")).toBeInTheDocument();
    expect(screen.getAllByTestId("desktop-activity-expert")).toHaveLength(1);
    expect(screen.getAllByTestId("desktop-activity-window")).toHaveLength(1);
  });
});
