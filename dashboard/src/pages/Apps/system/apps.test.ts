import { describe, expect, it } from "vitest";

import capabilitiesHtml from "./apps/capabilities.html?raw";
import connectionsHtml from "./apps/connections.html?raw";
import operationsHtml from "./apps/operations.html?raw";
import tasksHtml from "./apps/tasks.html?raw";
import usageHtml from "./apps/usage.html?raw";

describe("bundled system app pages", () => {
  it.each([
    ["usage", usageHtml, "usage.summary"],
    ["connections", connectionsHtml, "channels.list"],
    ["capabilities", capabilitiesHtml, "skills.list"],
    ["operations", operationsHtml, "agent.metrics"],
    ["tasks", tasksHtml, "cron.list"],
  ])("%s uses the restricted SDK and its own query", (_name, html, query) => {
    expect(html).toContain("octopSystem");
    expect(html).toContain(query);
    expect(html).not.toContain("octopApp");
    expect(html).not.toContain("fetch(");
  });

  it("uses only the declared write actions", () => {
    expect(capabilitiesHtml).toContain('"skills.toggle"');
    expect(tasksHtml).toContain('"cron.run"');
  });
});
