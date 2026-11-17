import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DesktopBrowserWindow, {
  normalizeBrowserUrl,
} from "./DesktopBrowserWindow";
import { isDashboardConsoleUrl } from "./desktopWindows";

vi.mock("react-i18next", () => {
  const t = (key: string, fallback?: string) => fallback ?? key;
  return { useTranslation: () => ({ t }) };
});

describe("normalizeBrowserUrl", () => {
  it("adds https and normalizes addresses", () => {
    expect(normalizeBrowserUrl("example.com")).toBe("https://example.com/");
    expect(normalizeBrowserUrl("http://localhost:8088/x")).toBe(
      "http://localhost:8088/x",
    );
    expect(normalizeBrowserUrl("")).toBe("");
  });

  it("rejects non-http schemes and garbage", () => {
    expect(normalizeBrowserUrl("ftp://example.com")).toBeNull();
    expect(normalizeBrowserUrl("javascript:alert(1)")).toBeNull();
    expect(normalizeBrowserUrl("not a url")).toBeNull();
  });
});

describe("isDashboardConsoleUrl", () => {
  it("flags same-origin SPA pages but not API pages", () => {
    expect(
      isDashboardConsoleUrl(`${window.location.origin}/apps/desktop`),
    ).toBe(true);
    expect(isDashboardConsoleUrl(`${window.location.origin}/api/docs`)).toBe(
      false,
    );
    expect(isDashboardConsoleUrl(`${window.location.origin}/p/token123`)).toBe(
      false,
    );
    expect(isDashboardConsoleUrl("https://example.com/apps")).toBe(false);
  });
});

describe("DesktopBrowserWindow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts blank and navigates to a typed address", () => {
    const onNavigate = vi.fn();
    render(
      <DesktopBrowserWindow
        windowId="browser:agent_1"
        agentId="agent_1"
        agentName="Sales expert"
        url=""
        onNavigate={onNavigate}
      />,
    );

    expect(
      screen.getByText("Enter a URL to start browsing"),
    ).toBeInTheDocument();
    const address = screen.getByLabelText("Address");
    fireEvent.change(address, { target: { value: "example.com" } });
    fireEvent.submit(address.closest("form")!);

    expect(onNavigate).toHaveBeenCalledWith(
      "browser:agent_1",
      "https://example.com/",
    );
  });

  it("renders the page in a sandboxed iframe", () => {
    render(
      <DesktopBrowserWindow
        windowId="browser:agent_1"
        agentId="agent_1"
        url="https://example.com/"
        onNavigate={vi.fn()}
      />,
    );

    const frame = screen.getByTestId("desktop-browser-frame");
    expect(frame).toHaveAttribute("src", "https://example.com/");
    expect(frame).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-same-origin allow-forms allow-modals",
    );
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("does not embed the dashboard itself until the user opts in", () => {
    const consoleUrl = `${window.location.origin}/apps/desktop`;
    render(
      <DesktopBrowserWindow
        windowId="browser:agent_1"
        agentId="agent_1"
        url={consoleUrl}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByTestId("desktop-console-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-browser-frame")).toBeNull();

    fireEvent.click(screen.getByText("Load here anyway"));

    expect(screen.getByTestId("desktop-browser-frame")).toHaveAttribute(
      "src",
      consoleUrl,
    );
  });

  it("shows an error for non-http addresses without navigating", () => {
    const onNavigate = vi.fn();
    render(
      <DesktopBrowserWindow agentId="agent_1" url="" onNavigate={onNavigate} />,
    );

    const address = screen.getByLabelText("Address");
    fireEvent.change(address, { target: { value: "ftp://example.com" } });
    fireEvent.submit(address.closest("form")!);

    expect(
      screen.getByText("Enter a valid http(s) address."),
    ).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
  });
});
