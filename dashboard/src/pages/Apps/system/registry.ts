import {
  BarChart3,
  BriefcaseBusiness,
  Link2,
  Sparkles,
  Timer,
} from "lucide-react";

import { userCanAny, type PermissionHolder } from "../../../utils/permissions";
import type {
  SystemAppDefinition,
  SystemAppKey,
  SystemAppVisibilityAgent,
} from "./types";

/**
 * Platform-owned system apps that may appear on an expert desktop.
 *
 * This catalog is intentionally frontend-only: these apps do not have rows,
 * collaborators or public links. They read live data through the host bridge
 * and therefore need no `light_apps` migration.
 */
export const SYSTEM_APPS: readonly SystemAppDefinition[] = [
  {
    key: "usage",
    titleKey: "apps.system.usage.title",
    descriptionKey: "apps.system.usage.description",
    icon: BarChart3,
    permissions: ["token_usage"],
    queryKeys: ["usage.summary", "agent.metrics"],
    loadHtml: () => import("./apps/usage.html?raw").then((mod) => mod.default),
  },
  {
    key: "connections",
    titleKey: "apps.system.connections.title",
    descriptionKey: "apps.system.connections.description",
    icon: Link2,
    permissions: ["channels", "connectors"],
    queryKeys: ["channels.list", "connectors.list"],
    loadHtml: () =>
      import("./apps/connections.html?raw").then((mod) => mod.default),
  },
  {
    key: "capabilities",
    titleKey: "apps.system.capabilities.title",
    descriptionKey: "apps.system.capabilities.description",
    icon: Sparkles,
    permissions: ["skills", "tools"],
    queryKeys: ["skills.list", "tools.summary"],
    actions: ["skills.toggle"],
    loadHtml: () =>
      import("./apps/capabilities.html?raw").then((mod) => mod.default),
  },
  {
    key: "operations",
    titleKey: "apps.system.operations.title",
    descriptionKey: "apps.system.operations.description",
    icon: BriefcaseBusiness,
    permissions: [],
    queryKeys: ["agent.metrics", "agent.inbox", "agent.status"],
    loadHtml: () =>
      import("./apps/operations.html?raw").then((mod) => mod.default),
  },
  {
    key: "tasks",
    titleKey: "apps.system.tasks.title",
    descriptionKey: "apps.system.tasks.description",
    icon: Timer,
    permissions: ["tasks"],
    queryKeys: ["cron.list"],
    actions: ["cron.run"],
    loadHtml: () => import("./apps/tasks.html?raw").then((mod) => mod.default),
  },
];

export const SYSTEM_APP_BY_KEY: ReadonlyMap<SystemAppKey, SystemAppDefinition> =
  new Map(SYSTEM_APPS.map((app) => [app.key, app]));

export function isSystemAppKey(value: string): value is SystemAppKey {
  return SYSTEM_APP_BY_KEY.has(value as SystemAppKey);
}

/** Deny-by-default: a system app may only call the queries it declares. */
export function systemAppAllowsQuery(
  appKey: SystemAppKey,
  queryKey: string,
): boolean {
  return SYSTEM_APP_BY_KEY.get(appKey)?.queryKeys.includes(queryKey) ?? false;
}

/** Write actions are separately declared and always owner-confirmed. */
export function systemAppAllowsAction(
  appKey: SystemAppKey,
  actionKey: string,
): boolean {
  return SYSTEM_APP_BY_KEY.get(appKey)?.actions?.includes(actionKey) ?? false;
}

interface VisibilityOptions {
  agent: SystemAppVisibilityAgent;
  user: PermissionHolder | null | undefined;
  /** Admins bypass the owner check (same semantics as the dashboard). */
  isAdmin?: boolean;
}

/**
 * Which system apps belong on an expert's desktop.
 *
 * Owner/admin only; team hosts keep their own configuration surface. Module
 * permissions mirror the sidebar so an app never advertises a page the user
 * cannot open.
 */
export function visibleSystemApps({
  agent,
  user,
  isAdmin = false,
}: VisibilityOptions): SystemAppDefinition[] {
  if (!agent || agent.kind === "team") return [];
  if (!isAdmin && !agent.is_owner) return [];
  return SYSTEM_APPS.filter(
    (app) => app.permissions.length === 0 || userCanAny(user, app.permissions),
  );
}
