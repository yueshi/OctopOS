import { fireEvent, render, screen, within } from "@testing-library/react";
import { BarChart3 } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

import AllAppsGallery, { type SystemAppGalleryItem } from "./AllAppsGallery";
import type { SystemAppDefinition } from "./system/types";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
    i18n: { language: "en" },
  }),
}));

const usage: SystemAppDefinition = {
  key: "usage",
  titleKey: "apps.system.usage.title",
  descriptionKey: "apps.system.usage.description",
  icon: BarChart3,
  permissions: [],
  queryKeys: [],
  loadHtml: async () => "",
};

const systemItem: SystemAppGalleryItem = {
  agentId: "agent_1",
  agentName: "Sales expert",
  definition: usage,
};

describe("AllAppsGallery system apps", () => {
  it("lists system apps under their expert and opens the desktop", () => {
    const onOpenSystemApp = vi.fn();
    render(
      <AllAppsGallery
        apps={[]}
        systemApps={[systemItem]}
        timeZone="UTC"
        onOpen={() => undefined}
        onOpenSystemApp={onOpenSystemApp}
      />,
    );

    const card = screen.getByTestId("all-apps-system-card");
    expect(card).toHaveTextContent("Sales expert");
    fireEvent.click(card);
    expect(onOpenSystemApp).toHaveBeenCalledWith("agent_1", "usage");
  });

  it("filters between MiniApps and system apps", () => {
    render(
      <AllAppsGallery
        apps={[]}
        systemApps={[systemItem]}
        timeZone="UTC"
        onOpen={() => undefined}
        onOpenSystemApp={() => undefined}
      />,
    );

    const filter = screen.getByTestId("all-apps-filter");
    fireEvent.click(within(filter).getByText("MiniApps"));
    expect(
      screen.queryByTestId("all-apps-system-card"),
    ).not.toBeInTheDocument();

    fireEvent.click(within(filter).getByText("System apps"));
    expect(screen.getByTestId("all-apps-system-card")).toBeInTheDocument();
  });
});
