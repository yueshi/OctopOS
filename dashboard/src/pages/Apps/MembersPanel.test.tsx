import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppInvite, AppMember, AppTable } from "../../api/modules/apps";
import MembersPanel from "./MembersPanel";

const api = vi.hoisted(() => ({
  members: vi.fn(),
  invites: vi.fn(),
  joinCodes: vi.fn(),
  externalSharing: vi.fn(),
  updateExternalSharing: vi.fn(),
  setTableMemberVisible: vi.fn(),
  addMember: vi.fn(),
  addGuest: vi.fn(),
  updateMember: vi.fn(),
  removeMember: vi.fn(),
  createInvite: vi.fn(),
  revokeInvite: vi.fn(),
  updateInviteHost: vi.fn(),
  createJoinCode: vi.fn(),
  revokeJoinCode: vi.fn(),
}));

const copy = vi.hoisted(() => ({ copyText: vi.fn(async () => true) }));

vi.mock("../../api/modules/apps", () => ({ appsApi: api }));
vi.mock("../../utils/copyText", () => copy);
vi.mock("../../utils/antdMessage", () => ({
  message: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("react-i18next", async () => {
  const zh = (await import("../../locales/zh.json")).default;
  const lookup = (key: string): unknown => {
    let value: unknown = zh;
    for (const part of key.split(".")) {
      value = (value as Record<string, unknown> | undefined)?.[part];
    }
    return value;
  };
  const t = (key: string, options?: unknown): string => {
    const value = lookup(key);
    if (typeof value !== "string") return key;
    if (options && typeof options === "object" && !Array.isArray(options)) {
      return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String((options as Record<string, unknown>)[name] ?? ""),
      );
    }
    return value;
  };
  return { useTranslation: () => ({ t, i18n: { language: "zh" } }) };
});

const member: AppMember = {
  user_id: 2,
  username: "bob",
  display_name: "Bob",
  kind: "guest",
  permission: "participate",
  status: "pending",
  joined_via: "invite",
  invited_by: 1,
  created_at: 0,
  last_active_at: 0,
};

const invite: AppInvite = {
  invite_id: "inv_1",
  invite_url: "/join?invite=tok",
  audience: "guest",
  permission: "participate",
  expires_at: 1_800_000_000,
  max_uses: 1,
  used_count: 0,
  host_status: "pending",
  revoked_at: null,
};

const table: AppTable = {
  table_key: "main",
  label: "主表",
  row_count: 3,
  is_main: true,
  public_visible: false,
  member_visible: false,
  columns: [],
};

function renderPanel(canManage: boolean, hostView = false) {
  return render(
    <AntdApp>
      <MembersPanel
        appId="app_1"
        agentId="agent_1"
        tables={[table]}
        canManage={canManage}
        hostView={hostView}
      />
    </AntdApp>,
  );
}

describe("MembersPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.members.mockResolvedValue({ members: [member] });
    api.invites.mockResolvedValue({ invites: [invite] });
    api.joinCodes.mockResolvedValue({ join_codes: [] });
    api.externalSharing.mockResolvedValue({
      allow_external_participants: true,
      max_guests_per_app: 50,
      require_host_approval: true,
      can_manage: true,
    });
    api.updateMember.mockResolvedValue({ ...member, status: "active" });
    api.setTableMemberVisible.mockResolvedValue({
      ...table,
      member_visible: true,
    });
  });

  it("approves a pending member and exposes table visibility", async () => {
    const onTableUpdated = vi.fn();
    render(
      <AntdApp>
        <MembersPanel
          appId="app_1"
          agentId="agent_1"
          tables={[table]}
          canManage
          onTableUpdated={onTableUpdated}
        />
      </AntdApp>,
    );

    expect(await screen.findByText("Bob")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /批\s*准/ }));

    await waitFor(() =>
      expect(api.updateMember).toHaveBeenCalledWith("app_1", 2, {
        status: "active",
      }),
    );

    await userEvent.click(screen.getByRole("tab", { name: "访问" }));
    const toggles = await screen.findAllByRole("switch");
    await userEvent.click(toggles[toggles.length - 1]);
    await waitFor(() =>
      expect(api.setTableMemberVisible).toHaveBeenCalledWith(
        "app_1",
        "main",
        false,
        true,
      ),
    );
    expect(onTableUpdated).toHaveBeenCalled();
  });

  it("lets the agent host approve without managing roles or join codes", async () => {
    api.externalSharing.mockResolvedValue({
      allow_external_participants: true,
      max_guests_per_app: 50,
      require_host_approval: true,
      can_manage: false,
    });
    renderPanel(false, true);

    expect(
      await screen.findByText(/你是托管该应用的 Agent 所有者/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "加入码" }),
    ).not.toBeInTheDocument();
    expect(api.joinCodes).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /添加成员/ })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /批\s*准/ }));
    await waitFor(() =>
      expect(api.updateMember).toHaveBeenCalledWith("app_1", 2, {
        status: "active",
      }),
    );
  });
});
