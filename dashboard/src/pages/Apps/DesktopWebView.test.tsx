import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DesktopWebView from "./DesktopWebView";

vi.mock("react-i18next", () => {
  const t = (key: string, fallback?: string) => fallback ?? key;
  return { useTranslation: () => ({ t }) };
});

describe("DesktopWebView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads external pages with cookies and same-origin module scripts", () => {
    render(
      <DesktopWebView
        windowId="external:agent_1:url"
        url="https://example.com/path"
      />,
    );

    const frame = screen.getByTestId("desktop-webview-frame");
    expect(frame).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-same-origin allow-forms allow-modals",
    );
    expect(frame).toHaveAttribute("src", "https://example.com/path");
  });

  it("guards the console and loads it only on request", () => {
    const url = `${window.location.origin}/apps/desktop`;
    render(<DesktopWebView windowId="external:agent_1:url" url={url} />);

    expect(screen.getByTestId("desktop-console-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-webview-frame")).toBeNull();

    fireEvent.click(screen.getByText("Load here anyway"));

    expect(screen.getByTestId("desktop-webview-frame")).toHaveAttribute(
      "src",
      url,
    );
  });

  it("supports closing and opening the page in the system browser", () => {
    const onClose = vi.fn();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(
      <DesktopWebView
        windowId="external:agent_1:url"
        url="https://example.com/path"
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByLabelText("Back"));
    expect(onClose).toHaveBeenCalledWith("external:agent_1:url");

    fireEvent.click(screen.getByLabelText("More"));
    fireEvent.click(screen.getByText("Open in browser"));
    expect(open).toHaveBeenCalledWith(
      "https://example.com/path",
      "_blank",
      "noopener,noreferrer",
    );
  });
});
