import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DesktopConnectorsPanel from "./DesktopConnectorsPanel";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      typeof fallback === "string" ? fallback : key,
    i18n: { language: "en" },
  }),
}));

vi.mock("../../Agent/Connectors/useConnectors", () => ({
  useConnectorInstances: () => ({
    catalog: [
      {
        kind: "feishu",
        name: "Feishu",
        description: "Docs",
        category: "messaging",
        phase: "available",
      },
    ],
    instances: [],
    loading: false,
    refresh: vi.fn(),
  }),
}));

vi.mock("../../Agent/Connectors/ConnectorCard", () => ({
  ConnectorCard: ({
    entry,
    onConfigure,
  }: {
    entry: { name: string; kind: string };
    onConfigure: (entry: unknown, instance: null) => void;
  }) => (
    <button
      type="button"
      data-testid="catalog-card"
      onClick={() => onConfigure(entry, null)}
    >
      {entry.name}
    </button>
  ),
}));

vi.mock("../../Agent/Connectors/ConnectorInstanceCard", () => ({
  ConnectorInstanceCard: () => <div data-testid="instance-card" />,
}));

vi.mock("../../Agent/Connectors/CustomMcpTab", () => ({
  CustomMcpTab: () => <div data-testid="custom-mcp" />,
}));

vi.mock("../../Agent/Connectors", () => ({
  ConnectorConfigDrawer: ({
    open,
    entry,
  }: {
    open: boolean;
    entry: { kind: string } | null;
  }) => (
    <div
      data-testid="connector-drawer"
      data-open={open ? "true" : "false"}
      data-kind={entry?.kind ?? ""}
    />
  ),
}));

describe("DesktopConnectorsPanel", () => {
  it("switches between connected, catalogue and custom MCP views", () => {
    render(<DesktopConnectorsPanel />);

    expect(screen.queryByTestId("instance-card")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    const catalogCard = screen.getByTestId("catalog-card");
    expect(catalogCard).toHaveTextContent("Feishu");
    fireEvent.click(catalogCard);
    expect(screen.getByTestId("connector-drawer")).toHaveAttribute(
      "data-kind",
      "feishu",
    );

    fireEvent.click(screen.getByRole("button", { name: "Custom MCP" }));
    expect(screen.getByTestId("custom-mcp")).toBeInTheDocument();
  });
});
