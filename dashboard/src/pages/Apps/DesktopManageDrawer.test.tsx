import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AppSummary } from "../../api/modules/apps";
import type { SystemAppDefinition } from "./system/types";
import DesktopManageDrawer from "./DesktopManageDrawer";
import { EMPTY_DESKTOP_ORG } from "./desktopOrg";

const app: AppSummary = {
  app_id: "app_1",
  name: "客户台账",
  description: "",
  agent_id: "agent_1",
  agent_name: "销售助理",
  owner_user_id: 1,
  owner_username: "owner",
  permission: "edit_structure",
  row_count: 5,
  html_bytes: 10,
  data_bytes: 10,
  has_html: true,
  has_preview: false,
  created_at: 0,
  updated_at: 0,
};

const systemApp: SystemAppDefinition = {
  key: "usage",
  titleKey: "apps.system.usage.title",
  descriptionKey: "apps.system.usage.description",
  icon: () => null,
  permissions: [],
  queryKeys: [],
  loadHtml: async () => "",
};

function renderDrawer(overrides = {}) {
  const onChange = vi.fn();
  render(
    <DesktopManageDrawer
      open
      onClose={vi.fn()}
      agentId="agent_1"
      agentName="销售助理"
      userApps={[app]}
      systemApps={[systemApp]}
      prefs={EMPTY_DESKTOP_ORG}
      defaultPinnedIds={["app_1"]}
      knownEntryIds={["app:app_1"]}
      onChange={onChange}
      {...overrides}
    />,
  );
  return { onChange };
}

describe("DesktopManageDrawer", () => {
  it("hides a system app for this expert", () => {
    const { onChange } = renderDrawer();
    const row = screen.getByTestId("desktop-manage-system-usage");

    fireEvent.click(within(row).getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        hiddenSystem: { agent_1: ["usage"] },
      }),
    );
  });

  it("hides a mini app and unpins it", () => {
    const { onChange } = renderDrawer({
      prefs: { hiddenSystem: {}, hiddenApps: [], pinnedApps: ["app_1"] },
    });
    const row = screen.getByTestId("desktop-manage-app-app_1");
    const switches = within(row).getAllByRole("switch");

    fireEvent.click(switches[0]);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ hiddenApps: ["app_1"], pinnedApps: [] }),
    );
  });

  it("unpins an app from the dock", () => {
    const { onChange } = renderDrawer();
    const row = screen.getByTestId("desktop-manage-app-app_1");
    const switches = within(row).getAllByRole("switch");

    fireEvent.click(switches[1]);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ pinnedApps: [] }),
    );
  });

  it("resets the organization", () => {
    const { onChange } = renderDrawer({
      prefs: { hiddenSystem: { agent_1: ["usage"] }, hiddenApps: ["app_1"] },
    });

    fireEvent.click(screen.getByRole("button", { name: /Reset/ }));
    expect(onChange).toHaveBeenCalledWith({
      hiddenSystem: {},
      hiddenApps: [],
    });
  });

  it("opens the folder editor prefilled", () => {
    renderDrawer({
      prefs: {
        hiddenSystem: {},
        hiddenApps: [],
        folders: [{ id: "f1", name: "客户", appIds: ["app_1"] }],
        order: ["folder:f1"],
      },
    });

    const row = screen.getByTestId("desktop-manage-folder-f1");
    fireEvent.click(within(row).getByRole("button", { name: "Edit folder" }));
    expect(screen.getByDisplayValue("客户")).toBeInTheDocument();
    expect(screen.getByText("Edit folder")).toBeInTheDocument();
  });

  it("deletes a folder and returns its apps to the desktop", () => {
    const { onChange } = renderDrawer({
      prefs: {
        hiddenSystem: {},
        hiddenApps: [],
        folders: [{ id: "f1", name: "客户", appIds: ["app_1"] }],
        order: ["folder:f1"],
      },
    });

    const row = screen.getByTestId("desktop-manage-folder-f1");
    fireEvent.click(within(row).getByRole("button", { name: "Delete folder" }));
    expect(onChange).toHaveBeenCalledWith({
      hiddenSystem: {},
      hiddenApps: [],
    });
  });
});
