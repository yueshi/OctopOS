import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GuestMyPage from "./My";

const api = vi.hoisted(() => ({
  guestApps: vi.fn(),
  guestAppSession: vi.fn(),
  leaveGuestApp: vi.fn(),
  refreshGuestSession: vi.fn(),
  revokeGuestSession: vi.fn(),
  deleteGuestAccount: vi.fn(),
}));

const session = vi.hoisted(() => ({
  hasGuestSession: vi.fn(() => true),
  getGuestRefreshToken: vi.fn(() => ""),
  isGuestSessionRemembered: vi.fn(() => true),
  setGuestSession: vi.fn(),
  clearGuestSession: vi.fn(),
  setAppToken: vi.fn(),
}));

vi.mock("../../api/modules/apps", () => ({ appsApi: api }));
vi.mock("../../api/request", () => session);
vi.mock("../../utils/antdMessage", () => ({
  message: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
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

function renderPage() {
  return render(
    <AntdApp>
      <MemoryRouter initialEntries={["/my"]}>
        <Routes>
          <Route path="/my" element={<GuestMyPage />} />
          <Route path="/apps/:appId" element={<div>runtime-app</div>} />
        </Routes>
      </MemoryRouter>
    </AntdApp>,
  );
}

describe("GuestMyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    session.hasGuestSession.mockReturnValue(true);
    session.getGuestRefreshToken.mockReturnValue("");
    session.isGuestSessionRemembered.mockReturnValue(true);
  });

  it("lists joined apps and mints an app token before opening one", async () => {
    api.guestApps.mockResolvedValue({
      apps: [
        {
          app_id: "app_1",
          name: "销售台账",
          role: "participate",
          last_opened_at: 0,
        },
      ],
    });
    api.guestAppSession.mockResolvedValue({
      access_token: "app-token",
      token_type: "bearer",
      expires_in: 7200,
      scope: "app",
      app_id: "app_1",
      role: "participate",
    });
    renderPage();

    expect(await screen.findByText("销售台账")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /打\s*开/ }));

    await waitFor(() =>
      expect(api.guestAppSession).toHaveBeenCalledWith("app_1"),
    );
    expect(session.setAppToken).toHaveBeenCalledWith(
      "app_1",
      "app-token",
      7200,
    );
    expect(await screen.findByText("runtime-app")).toBeInTheDocument();
  });

  it("refreshes an expired session once before showing an error", async () => {
    api.guestApps
      .mockRejectedValueOnce(
        new Error(
          'Request failed: 401 Unauthorized - {"error":{"code":"APP_SESSION_INVALID","message":"expired"}}',
        ),
      )
      .mockResolvedValueOnce({
        apps: [
          {
            app_id: "app_1",
            name: "销售台账",
            role: "participate",
            last_opened_at: 0,
          },
        ],
      });
    session.getGuestRefreshToken.mockReturnValue("refresh-token");
    api.refreshGuestSession.mockResolvedValue({
      guest_token: "new-guest",
      refresh_token: "new-refresh",
      guest_expires_in: 604800,
      refresh_expires_in: 2592000,
    });
    renderPage();

    expect(await screen.findByText("销售台账")).toBeInTheDocument();
    expect(api.refreshGuestSession).toHaveBeenCalledWith("refresh-token");
    expect(session.setGuestSession).toHaveBeenCalledWith(
      expect.objectContaining({
        guestToken: "new-guest",
        refreshToken: "new-refresh",
        remember: true,
      }),
    );
    expect(api.guestApps).toHaveBeenCalledTimes(2);
  });

  it("leaves an app after confirmation", async () => {
    api.guestApps.mockResolvedValue({
      apps: [
        {
          app_id: "app_1",
          name: "销售台账",
          role: "participate",
          last_opened_at: 0,
        },
      ],
    });
    api.leaveGuestApp.mockResolvedValue(undefined);
    renderPage();

    await screen.findByText("销售台账");
    await userEvent.click(screen.getByRole("button", { name: /^退\s*出$/ }));
    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() =>
      expect(api.leaveGuestApp).toHaveBeenCalledWith("app_1"),
    );
    expect(
      await screen.findByText("你还没有加入任何应用。"),
    ).toBeInTheDocument();
  });

  it("shows the empty state without a guest session", () => {
    session.hasGuestSession.mockReturnValue(false);
    renderPage();
    expect(screen.getByText("没有访客会话")).toBeInTheDocument();
  });
});
