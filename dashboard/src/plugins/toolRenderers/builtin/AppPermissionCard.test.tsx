import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { appsApi } from "../../../api/modules/apps";
import type { ToolRenderProps } from "../types";
import { AppPermissionCard } from "./AppPermissionCard";

vi.mock("../../../api/modules/apps", () => ({
  appsApi: {
    accessRequests: vi.fn().mockResolvedValue({ requests: [] }),
    createAccessRequest: vi.fn(),
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

const host = {
  registerRenderer: vi.fn(),
  getToolContext: () => ({
    agentId: null,
    threadId: null,
    locale: "zh",
    theme: "light" as const,
  }),
  patchResult: vi.fn(),
  request: vi.fn(),
  openSidePanel: vi.fn(),
  closeSidePanel: vi.fn(),
  openGamePanel: vi.fn(),
} as unknown as ToolRenderProps["host"];

describe("AppPermissionCard", () => {
  beforeEach(() => {
    vi.mocked(appsApi.accessRequests).mockResolvedValue({ requests: [] });
  });

  it("shows the identities and the required permission", () => {
    render(
      <MemoryRouter>
        <AppPermissionCard
          pluginId="builtin"
          toolName="app_create"
          status="error"
          args={{}}
          data={{
            code: "APP_AGENT_NOT_ACCESSIBLE",
            message: "agent is not accessible to this user",
            app_id: "app_1",
            agent_id: "agent_1",
            agent_owner: "alice",
            app_owner: "jk",
            acting_user: "bob",
            needed: "edit_structure",
          }}
          isStreaming={false}
          host={host}
        />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("app-permission-card")).toBeInTheDocument();
    expect(screen.getByText("Permission required")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("jk")).toBeInTheDocument();
    expect(screen.getByText("Edit structure")).toBeInTheDocument();
    expect(
      screen.getByText(/Ask the owner to share the expert/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Request access" }),
    ).toBeInTheDocument();
  });

  it("restores the pending status of an earlier request", async () => {
    vi.mocked(appsApi.accessRequests).mockResolvedValue({
      requests: [
        {
          request_id: "req_1",
          app_id: "app_1",
          agent_id: "agent_1",
          requester_username: "bob",
          app_owner_username: "jk",
          agent_owner_username: "jk",
          needed: "edit_structure",
          note: "",
          status: "pending",
          created_at: 1,
          resolved_at: null,
        },
      ],
    });

    render(
      <MemoryRouter>
        <AppPermissionCard
          pluginId="builtin"
          toolName="app_create"
          status="error"
          args={{}}
          data={{
            app_id: "app_1",
            agent_id: "agent_1",
            agent_owner: "alice",
            app_owner: "jk",
            acting_user: "bob",
            needed: "edit_structure",
          }}
          isStreaming={false}
          host={host}
        />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("Request submitted; waiting for the owner"),
    ).toBeInTheDocument();
  });

  it("offers owner actions when the viewer owns the expert or app", () => {
    const { rerender } = render(
      <MemoryRouter>
        <AppPermissionCard
          pluginId="builtin"
          toolName="app_create"
          status="error"
          args={{}}
          data={{
            app_id: "app_1",
            agent_id: "agent_1",
            agent_owner: "alice",
            app_owner: "bob",
            acting_user: "alice",
            needed: "edit_data",
            agent_shared: false,
          }}
          isStreaming={false}
          host={host}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("button", { name: "Share expert" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Request access" }),
    ).not.toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <AppPermissionCard
          pluginId="builtin"
          toolName="app_create"
          status="error"
          args={{}}
          data={{
            app_id: "app_1",
            agent_id: "agent_1",
            agent_owner: "alice",
            app_owner: "bob",
            acting_user: "bob",
            needed: "edit_data",
            agent_shared: true,
          }}
          isStreaming={false}
          host={host}
        />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("button", { name: "Manage collaborators" }),
    ).toBeInTheDocument();
  });
});
