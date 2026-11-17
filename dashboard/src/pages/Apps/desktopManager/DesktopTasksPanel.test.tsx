import { render, screen, fireEvent } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { describe, expect, it, vi } from "vitest";

import type { CronJobSpecOutput } from "../../../api/types";
import DesktopTasksPanel from "./DesktopTasksPanel";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      typeof fallback === "string" ? fallback : key,
    i18n: { language: "en" },
  }),
}));

vi.mock("../../Control/CronJobs/useCronJobs", () => ({
  useCronJobs: () => ({
    jobs: [
      {
        id: "job_1",
        name: "Daily report",
        enabled: true,
        schedule: { cron: "0 9 * * *" },
      },
    ],
    loading: false,
    cronTimezone: "UTC",
    activeAgentId: "agent_1",
    createJob: vi.fn(),
    updateJob: vi.fn(),
    deleteJob: vi.fn(),
    toggleEnabled: vi.fn(),
    executeNow: vi.fn(),
    jobToFormValues: vi.fn(() => ({})),
    refetchJobs: vi.fn(),
  }),
}));

vi.mock("../../Control/CronJobs/components", () => ({
  CronJobCard: ({ job: row }: { job: CronJobSpecOutput }) => (
    <div data-testid="cron-card">{row.name}</div>
  ),
  JobDrawer: ({ open }: { open: boolean }) => (
    <div data-testid="job-drawer" data-open={open ? "true" : "false"} />
  ),
  ExecuteNowModal: ({ open }: { open: boolean }) => (
    <div data-testid="execute-modal" data-open={open ? "true" : "false"} />
  ),
}));

describe("DesktopTasksPanel", () => {
  it("lists tasks and opens the native editor", () => {
    render(
      <AntdApp>
        <DesktopTasksPanel />
      </AntdApp>,
    );

    expect(screen.getByTestId("cron-card")).toHaveTextContent("Daily report");
    expect(screen.getByTestId("job-drawer")).toHaveAttribute(
      "data-open",
      "false",
    );

    fireEvent.click(screen.getByRole("button", { name: "Create Job" }));
    expect(screen.getByTestId("job-drawer")).toHaveAttribute(
      "data-open",
      "true",
    );
  });
});
