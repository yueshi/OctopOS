import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceArtifact } from "../../api/modules/workspace";
import DesktopFilesWindow from "./DesktopFilesWindow";

const {
  mockListArtifacts,
  mockListTree,
  mockReadFile,
  mockDownload,
  mockCreateSession,
} = vi.hoisted(() => ({
  mockListArtifacts: vi.fn(),
  mockListTree: vi.fn(),
  mockReadFile: vi.fn(),
  mockDownload: vi.fn(),
  mockCreateSession: vi.fn(),
}));

vi.mock("../../api/modules/workspace", () => ({
  workspaceApi: {
    listAgentArtifacts: mockListArtifacts,
    listAgentTree: mockListTree,
    readAgentFile: mockReadFile,
    downloadAgentFile: mockDownload,
    createHtmlPreviewSession: mockCreateSession,
  },
}));

vi.mock("../../api/config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../../utils/antdMessage", () => ({
  message: { error: vi.fn() },
}));

vi.mock("../../utils/saveBlobAsFile", () => ({
  saveBlobAsFile: vi.fn(),
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("react-i18next", () => {
  const t = (key: string, fallback?: string) => fallback ?? key;
  return { useTranslation: () => ({ t }) };
});

vi.mock("../Agent/Workspace/components/FileViewer", () => ({
  default: ({ path }: { path: string }) => (
    <div data-testid="file-viewer" data-path={path} />
  ),
}));

const htmlArtifact: WorkspaceArtifact = {
  path: "outbound/report.html",
  name: "report.html",
  agent_id: "agent_1",
  thread_id: "thr_1",
  thread_title: "Weekly report",
  last_active: 1_700_000_000,
  in_workspace: true,
};

describe("DesktopFilesWindow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListTree.mockResolvedValue([]);
    mockReadFile.mockResolvedValue({ path: "", content: "" });
    mockCreateSession.mockResolvedValue({
      token: "tok",
      base_path: "/workspace-preview/tok/",
      entry: "report.html",
      expires_at: 1_700_000_600,
    });
  });

  it("renders generated HTML through the capability URL", async () => {
    mockListArtifacts.mockResolvedValue({ items: [htmlArtifact] });
    render(
      <DesktopFilesWindow
        agentId="agent_1"
        agentName="Sales expert"
        threadId="thr_1"
      />,
    );

    expect(await screen.findByText("report.html")).toBeInTheDocument();
    expect(screen.getByText("This conversation")).toBeInTheDocument();

    fireEvent.click(screen.getByText("report.html"));
    const frame = await screen.findByTestId("desktop-files-html-frame");
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

  it("opens HTML in an independent window by default", async () => {
    mockListArtifacts.mockResolvedValue({ items: [htmlArtifact] });
    const onOpenHtml = vi.fn();
    render(
      <DesktopFilesWindow
        agentId="agent_1"
        agentName="Sales expert"
        onOpenHtml={onOpenHtml}
      />,
    );

    fireEvent.click(await screen.findByText("report.html"));

    expect(onOpenHtml).toHaveBeenCalledWith({
      agentId: "agent_1",
      path: "outbound/report.html",
      name: "report.html",
      fromWorkspace: true,
    });
    expect(
      screen.getByTestId("desktop-files-html-external"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("desktop-files-html-frame"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Preview here"));
    expect(
      await screen.findByTestId("desktop-files-html-frame"),
    ).toBeInTheDocument();
  });

  it("shows an empty state when there are no generated files", async () => {
    mockListArtifacts.mockResolvedValue({ items: [] });
    render(<DesktopFilesWindow agentId="agent_1" agentName="Sales expert" />);

    expect(
      await screen.findByText("This expert has not generated any files yet."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Could not load generated files."),
    ).not.toBeInTheDocument();
  });

  it("offers the workspace when generated files fail to load", async () => {
    mockListArtifacts.mockRejectedValue(new Error("404"));
    render(<DesktopFilesWindow agentId="agent_1" agentName="Sales expert" />);

    expect(
      await screen.findByText("Could not load generated files."),
    ).toBeInTheDocument();
    expect(screen.getByText("Browse all files")).toBeInTheDocument();
  });

  it("hides the all-files tab for shared viewers", async () => {
    mockListArtifacts.mockResolvedValue({ items: [htmlArtifact] });
    render(
      <DesktopFilesWindow
        agentId="agent_1"
        agentName="Sales expert"
        canBrowseAll={false}
      />,
    );

    await screen.findByText("report.html");
    expect(screen.queryByText("All files")).not.toBeInTheDocument();
    expect(mockListTree).not.toHaveBeenCalled();
  });

  it("opens text files in the shared FileViewer", async () => {
    const textArtifact: WorkspaceArtifact = {
      ...htmlArtifact,
      path: "outbound/notes.md",
      name: "notes.md",
    };
    mockListArtifacts.mockResolvedValue({ items: [textArtifact] });
    mockReadFile.mockResolvedValue({
      path: "outbound/notes.md",
      content: "# Notes",
    });

    render(<DesktopFilesWindow agentId="agent_1" agentName="Sales expert" />);

    fireEvent.click(await screen.findByText("notes.md"));
    const viewer = await screen.findByTestId("file-viewer");
    expect(viewer).toHaveAttribute("data-path", "outbound/notes.md");
  });
});
