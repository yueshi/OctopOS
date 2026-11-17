import { createContext, useContext, type ReactNode } from "react";

import type { OctopPluginUIHost } from "./types";

/**
 * Per-surface overrides for the plugin UI host's window actions.
 *
 * The builtin host is a module singleton wired by the chat page. A desktop
 * chat window lives in its own container and may coexist with other windows,
 * so it provides these actions through React context instead of the global
 * dock handlers. Renderers keep calling `host.openSidePanel` unchanged.
 */
export type ToolHostOverrides = Pick<
  OctopPluginUIHost,
  "openSidePanel" | "closeSidePanel" | "openGamePanel"
>;

const ToolHostScopeContext = createContext<ToolHostOverrides | null>(null);

export function ToolHostScopeProvider({
  overrides,
  children,
}: {
  overrides: ToolHostOverrides;
  children: ReactNode;
}) {
  return (
    <ToolHostScopeContext.Provider value={overrides}>
      {children}
    </ToolHostScopeContext.Provider>
  );
}

export function useToolHostOverrides(): ToolHostOverrides | null {
  return useContext(ToolHostScopeContext);
}
