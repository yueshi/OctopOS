import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DesktopHtmlWindow from "./DesktopHtmlWindow";

const { mockCreateSession, mockReadFile, mockDownload, mockSaveFile } =
  vi.hoisted(() => ({
    mockCreateSession: vi.fn(),
    mockReadFile: vi.fn(),
    mockDownload: vi.fn(),
    mockSaveFile: vi.fn(),
  }));

vi.mock("../../api/modules/workspace", () => ({
  workspaceApi: {
    createHtmlPreviewSession: mockCreateSession,
    readAgentFile: mockReadFile,
    downloadAgentFile: mockDownload,
  },
}));

vi.mock("../../api/config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../../utils/antdMessage", () => ({
  message: { error: vi.fn() },
}));

vi.mock("../../utils/saveBlobAsFile", () => ({
  saveBlobAsFile: mockSaveFile,
}));

vi.mock("react-i18next", () => {
  const t = (key: string, fallback?: string) => fallback ?? key;
  return { useTranslation: () => ({ t }) };
});

describe("DesktopHtmlWindow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateSession.mockResolvedValue({
      token: "tok",
      base_path: "/workspace-preview/tok/",
      entry: "report.html",
      expires_at: 1_700_000_600,
    });
    mockReadFile.mockResolvedValue({ path: "", content: "" });
    mockDownload.mockResolvedValue(new Blob(["html"], { type: "text/html" }));
  });

  it("renders the HTML page through the capability URL", async () => {
    render(
      <DesktopHtmlWindow
        agentId="agent_1"
        path="outbound/report.html"
        fromWorkspace
        title="report.html"
      />,
    );

    const frame = await screen.findByTestId("desktop-html-window-frame");
    await waitFor(() =>
      expect(frame).toHaveAttribute(
        "src",
        "/api/workspace-preview/tok/report.html",
      ),
    );
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-forms");
    expect(mockCreateSession).toHaveBeenCalledWith(
      "agent_1",
      "outbound/report.html",
    );
  });

  it("opens the Files window and downloads the current page", async () => {
    const onOpenFiles = vi.fn();
    render(
      <DesktopHtmlWindow
        agentId="agent_1"
        path="outbound/report.html"
        fromWorkspace
        title="report.html"
        onOpenFiles={onOpenFiles}
      />,
    );

    await screen.findByTestId("desktop-html-window-frame");
    fireEvent.click(screen.getByLabelText("Files"));
    expect(onOpenFiles).toHaveBeenCalledWith(
      "agent_1",
      "outbound/report.html",
      true,
    );

    fireEvent.click(screen.getByLabelText("Download"));
    await waitFor(() => expect(mockDownload).toHaveBeenCalled());
    expect(mockSaveFile).toHaveBeenCalled();
  });
});
