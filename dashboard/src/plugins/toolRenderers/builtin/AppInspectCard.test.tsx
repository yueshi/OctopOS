import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { appsApi, type SetupPlan } from "../../../api/modules/apps";
import type { ToolRenderProps } from "../types";
import { AppInspectCard } from "./AppInspectCard";

vi.mock("../../../api/modules/apps", () => ({
  appsApi: {
    getSetupPlan: vi.fn(),
    confirmSetupPlan: vi.fn(),
    executeSetupPlan: vi.fn(),
    cancelSetupPlan: vi.fn(),
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const host = {
  registerRenderer: vi.fn(),
  getToolContext: () => ({
    agentId: "agent_1",
    threadId: "thread_1",
    locale: "zh",
    theme: "light" as const,
  }),
  patchResult: vi.fn(),
  request: vi.fn(),
  openSidePanel: vi.fn(),
  closeSidePanel: vi.fn(),
  openGamePanel: vi.fn(),
} as unknown as ToolRenderProps["host"];

function plan(status: SetupPlan["status"]): SetupPlan {
  return {
    plan_id: "plan_1",
    status,
    revision: 1,
    name: "读书记录",
    file: "inbound/books.csv",
    sheet: "Sheet1",
    header_row: 0,
    columns: [
      {
        name: "title",
        label: "书名",
        type: "string",
        required: false,
        values: [],
      },
    ],
    include_pii: false,
    app_id: status === "dry_run_ready" ? "app_1" : null,
    dry_run:
      status === "dry_run_ready"
        ? { would_import: 2, skipped: 0, errors: [], blocked_pii: [] }
        : {},
    result: {},
    error: "",
    expires_at: null,
    report: {
      file: "inbound/books.csv",
      size_bytes: 20,
      sheets: [
        {
          name: "Sheet1",
          rows: 2,
          rows_scanned: 2,
          header_row: 0,
          header: ["title"],
          sample_rows: [["三体"]],
          columns: [
            {
              source: "title",
              name_suggestion: "title",
              type: "string",
              non_empty_ratio: 1,
              distinct: 2,
              samples: ["三体"],
              max_length: 3,
              pii: null,
            },
          ],
          warnings: [],
        },
      ],
      pii_columns: [],
    },
    allowed_actions:
      status === "dry_run_ready"
        ? ["execute", "cancel"]
        : ["confirm", "cancel"],
  };
}

function renderCard(data: unknown) {
  return render(
    <MemoryRouter>
      <AppInspectCard
        pluginId="builtin"
        toolName="app_propose_plan"
        status="done"
        args={{}}
        data={data}
        isStreaming={false}
        host={host}
      />
    </MemoryRouter>,
  );
}

describe("AppInspectCard", () => {
  it("confirms a draft plan and then offers the import", async () => {
    vi.mocked(appsApi.getSetupPlan).mockResolvedValue(plan("draft"));
    const ready = plan("dry_run_ready");
    delete ready.report;
    vi.mocked(appsApi.confirmSetupPlan).mockResolvedValue(ready);

    renderCard({ plan_id: "plan_1" });

    const confirm = await screen.findByRole("button", {
      name: "apps.planConfirm",
    });
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(appsApi.confirmSetupPlan).toHaveBeenCalledWith("plan_1", {
        revision: 1,
        name: "读书记录",
        columns: [
          {
            name: "title",
            label: "书名",
            type: "string",
            required: false,
            values: [],
          },
        ],
        include_pii: false,
        services: [],
        tables: [
          {
            key: "main",
            label: "读书记录",
            sheet: "Sheet1",
            header_row: 0,
            columns: [
              {
                name: "title",
                label: "书名",
                type: "string",
                required: false,
                values: [],
              },
            ],
            mapping: {},
            upsert_key: [],
            include_pii: false,
          },
        ],
      }),
    );
    expect(
      await screen.findByRole("button", { name: "apps.planExecute" }),
    ).toBeInTheDocument();
  });

  it("sends edited services and capabilities with the confirmation", async () => {
    const withExtras = plan("draft");
    withExtras.services = [
      {
        name: "papers",
        provider: "external",
        connector_instance_id: "",
        connector_kind: "http-extract",
        allowed_tools: ["fetch_json"],
        allowed_url_prefixes: ["https://api.example.com"],
        request_headers: {},
        cache_ttl_seconds: 300,
        max_calls_per_minute: 30,
        enabled: true,
      },
    ];
    withExtras.capabilities = ["data.read"];
    withExtras.allowed_capabilities = ["data.read", "data.write"];
    const ready = plan("dry_run_ready");
    ready.services = withExtras.services;
    ready.capabilities = withExtras.capabilities;
    ready.allowed_capabilities = withExtras.allowed_capabilities;
    delete ready.report;
    vi.mocked(appsApi.getSetupPlan).mockResolvedValue(withExtras);
    vi.mocked(appsApi.confirmSetupPlan).mockResolvedValue(ready);

    renderCard({ plan_id: "plan_1" });
    const confirm = await screen.findByRole("button", {
      name: "apps.planConfirm",
    });
    expect(screen.getByDisplayValue("papers")).toBeInTheDocument();
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(appsApi.confirmSetupPlan).toHaveBeenCalledWith(
        "plan_1",
        expect.objectContaining({
          services: [
            expect.objectContaining({
              name: "papers",
              provider: "external",
              allowed_tools: ["fetch_json"],
              allowed_url_prefixes: ["https://api.example.com"],
            }),
          ],
          capabilities: ["data.read"],
        }),
      ),
    );
  });

  it("renders a legacy inspection result read-only", () => {
    renderCard({
      file: "inbound/books.csv",
      size_bytes: 20,
      sheets: [
        {
          name: "Sheet1",
          rows: 2,
          header_row: 0,
          header: ["title"],
          columns: [
            {
              source: "title",
              name_suggestion: "title",
              type: "string",
              samples: ["三体"],
              pii: null,
            },
          ],
          warnings: [],
        },
      ],
    });

    expect(screen.getByText("apps.planReadonly")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "apps.planConfirm" }),
    ).not.toBeInTheDocument();
  });
});
