import { describe, expect, it } from "vitest";

import {
  systemAppAllowsAction,
  systemAppAllowsManager,
  systemAppAllowsQuery,
  visibleSystemApps,
} from "./registry";

const owner = { is_owner: true, kind: "expert" };
const shared = { is_owner: false, kind: "expert" };

describe("system app visibility", () => {
  it("shows permission-gated apps to the expert owner", () => {
    const apps = visibleSystemApps({
      agent: owner,
      user: {
        role: "user",
        permissions: ["token_usage", "channels", "skills", "tasks"],
      },
    });

    expect(apps.map((app) => app.key)).toEqual([
      "usage",
      "connections",
      "capabilities",
      "operations",
      "tasks",
    ]);
  });

  it("always keeps owner-only apps without a module permission", () => {
    const apps = visibleSystemApps({
      agent: owner,
      user: { role: "user", permissions: [] },
    });

    expect(apps.map((app) => app.key)).toEqual(["operations"]);
  });

  it("hides every system app from a collaborator who does not own the expert", () => {
    expect(
      visibleSystemApps({
        agent: shared,
        user: {
          role: "user",
          permissions: ["token_usage", "channels", "skills", "tools"],
        },
      }),
    ).toEqual([]);
  });

  it("hides system apps on team hosts", () => {
    expect(
      visibleSystemApps({
        agent: { is_owner: true, kind: "team" },
        user: { role: "admin", permissions: [] },
        isAdmin: true,
      }),
    ).toEqual([]);
  });

  it("lets admins inspect another owner's expert", () => {
    const apps = visibleSystemApps({
      agent: shared,
      user: { role: "admin", permissions: [] },
      isAdmin: true,
    });

    expect(apps.map((app) => app.key)).toContain("operations");
  });

  it("requires at least one of an app's module permissions", () => {
    const apps = visibleSystemApps({
      agent: owner,
      user: { role: "user", permissions: ["connectors"] },
    });

    expect(apps.map((app) => app.key)).toContain("connections");
    expect(apps.map((app) => app.key)).not.toContain("usage");
  });
});

describe("system app query allowlists", () => {
  it("lets each app call only its declared queries", () => {
    expect(systemAppAllowsQuery("usage", "usage.summary")).toBe(true);
    expect(systemAppAllowsQuery("usage", "agent.metrics")).toBe(true);
    expect(systemAppAllowsQuery("usage", "channels.list")).toBe(false);
    expect(systemAppAllowsQuery("connections", "connectors.list")).toBe(true);
    expect(systemAppAllowsQuery("operations", "agent.inbox")).toBe(true);
    expect(systemAppAllowsQuery("operations", "usage.summary")).toBe(false);
  });
});

describe("system app action allowlists", () => {
  it("lets each app call only its declared write actions", () => {
    expect(systemAppAllowsAction("capabilities", "skills.toggle")).toBe(true);
    expect(systemAppAllowsAction("capabilities", "cron.run")).toBe(false);
    expect(systemAppAllowsAction("tasks", "cron.run")).toBe(true);
    expect(systemAppAllowsAction("usage", "skills.toggle")).toBe(false);
  });
});

describe("system app manager allowlists", () => {
  it("opens only the native managers an app declares", () => {
    expect(systemAppAllowsManager("capabilities", "skills")).toBe(true);
    expect(systemAppAllowsManager("capabilities", "tools")).toBe(true);
    expect(systemAppAllowsManager("capabilities", "channels")).toBe(false);
    expect(systemAppAllowsManager("connections", "channels")).toBe(true);
    expect(systemAppAllowsManager("connections", "connectors")).toBe(true);
    expect(systemAppAllowsManager("tasks", "tasks")).toBe(true);
    expect(systemAppAllowsManager("operations", "tasks")).toBe(true);
    expect(systemAppAllowsManager("usage", "tasks")).toBe(false);
  });
});
