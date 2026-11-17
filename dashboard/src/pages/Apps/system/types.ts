import type { LucideIcon } from "lucide-react";

/** Platform-owned desktop apps. They are not `light_apps` records. */
export type SystemAppKey =
  | "usage"
  | "connections"
  | "capabilities"
  | "operations"
  | "tasks";

export interface SystemAppDefinition {
  key: SystemAppKey;
  /** i18n key for the window/icon label. */
  titleKey: string;
  /** i18n key for the hover description. */
  descriptionKey: string;
  icon: LucideIcon;
  /**
   * Any-of module permissions. Empty means "no extra module gate"; the app is
   * still restricted to the expert owner (or an admin).
   */
  permissions: readonly string[];
  /** Query keys this page may call. Deny-by-default per app. */
  queryKeys: readonly string[];
  /** Write actions this page may call after a host confirmation. */
  actions?: readonly string[];
  /** Bundled single-file HTML, loaded lazily with Vite's `?raw` import. */
  loadHtml: () => Promise<string>;
}

export interface SystemQueryContext {
  agentId: string;
}

export type SystemQueryParams = Record<
  string,
  string | number | boolean | null | undefined
>;

export type SystemAppVisibilityAgent = {
  is_owner?: boolean;
  kind?: string | null;
} | null;
