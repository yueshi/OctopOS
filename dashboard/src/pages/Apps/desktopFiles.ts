/** Shared types/helpers for desktop file and HTML windows. */

export interface DesktopFileRef {
  /** Producer agent; team conversations may surface another member's file. */
  agentId: string;
  path: string;
  name: string;
  fromWorkspace: boolean;
}

export function desktopFileBasename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

export function isHtmlPath(path: string): boolean {
  const ext = path.split(".").pop()?.toLowerCase();
  return ext === "html" || ext === "htm";
}

const ARTIFACT_TOOL_BASES = new Set([
  "write_file",
  "edit_file",
  "send_file",
  "send_file_to_user",
  "desktop_screenshot",
  "mobile_screenshot",
]);

/** Whether a tool call can change the generated-file ledger. */
export function isArtifactToolName(name: string): boolean {
  const base = name.toLowerCase().split("__").pop()?.split(":").pop();
  return Boolean(base && ARTIFACT_TOOL_BASES.has(base));
}
