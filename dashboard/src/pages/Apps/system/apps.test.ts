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

  it.each([
    ["usage", usageHtml],
    ["connections", connectionsHtml],
    ["capabilities", capabilitiesHtml],
    ["operations", operationsHtml],
    ["tasks", tasksHtml],
  ])("%s keeps navigation inside the desktop host", (_name, html) => {
    // Management entries must go through the host bridge, which opens a
    // native desktop panel. A direct <a href>, window.open or location change
    // would escape the desktop shell.
    expect(html).not.toMatch(/<a\s[^>]*href=/i);
    expect(html).not.toMatch(/(?:window|top|parent)\.open\s*\(/);
    expect(html).not.toMatch(/(?:top|parent|window|document)\.location\s*\./);
    expect(html).not.toMatch(/location\s*\.\s*(?:assign|replace)\s*\(/);

    const calls = html.match(/openManager\s*\(/g) ?? [];
    const hostCalls =
      html.match(/window\.octopSystem\.openManager\s*\(/g) ?? [];
    expect(hostCalls).toHaveLength(calls.length);
    expect(html).not.toContain("openConsole");
  });
});
