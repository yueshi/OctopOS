import type { AppSummary } from "../../api/modules/apps";
import type { SystemAppDefinition } from "./system/types";

export const DESKTOP_ORG_STORAGE_KEY = "octop:apps-desktop-org:v1";

export interface DesktopFolder {
  id: string;
  name: string;
  appIds: string[];
}

export type DesktopEntry =
  | { kind: "app"; app: AppSummary }
  | { kind: "folder"; folder: DesktopFolder; apps: AppSummary[] };

/**
 * Per-user desktop organization, kept in localStorage for this phase.
 *
 * - ``hiddenSystem`` is keyed by agent id so hiding the usage panel on one
 *   expert desktop does not hide it on every other desktop.
 * - ``pinnedApps === undefined`` means "use the default dock" (first N apps);
 *   an explicit array means the user has taken control of the dock.
 * - ``folders`` group user MiniApps into one desktop icon. An app can only be
 *   in one folder; ``order`` stores the top-level desktop order.
 */
export interface DesktopOrgPrefs {
  hiddenSystem: Record<string, string[]>;
  hiddenApps: string[];
  pinnedApps?: string[];
  folders?: DesktopFolder[];
  order?: string[];
}

export const EMPTY_DESKTOP_ORG: DesktopOrgPrefs = {
  hiddenSystem: {},
  hiddenApps: [],
};

function uniqueStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [
    ...new Set(
      value.filter(
        (item): item is string =>
          typeof item === "string" && item.trim().length > 0,
      ),
    ),
  ];
}

function parseHiddenSystem(value: unknown): Record<string, string[]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, string[]> = {};
  for (const [agentId, keys] of Object.entries(value)) {
    if (!agentId.trim()) continue;
    const clean = uniqueStrings(keys);
    if (clean.length > 0) out[agentId] = clean;
  }
  return out;
}

function parseFolders(value: unknown): DesktopFolder[] {
  if (!Array.isArray(value)) return [];
  const folders: DesktopFolder[] = [];
  const seenFolders = new Set<string>();
  const seenApps = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const row = item as Record<string, unknown>;
    const id = typeof row.id === "string" ? row.id.trim() : "";
    const name =
      typeof row.name === "string" ? row.name.trim().slice(0, 40) : "";
    if (!id || !name || seenFolders.has(id)) continue;
    const appIds = uniqueStrings(row.appIds).filter((appId) => {
      if (seenApps.has(appId)) return false;
      seenApps.add(appId);
      return true;
    });
    if (appIds.length === 0) continue;
    seenFolders.add(id);
    folders.push({ id, name, appIds });
  }
  return folders;
}

export function sanitizeDesktopOrg(value: unknown): DesktopOrgPrefs {
  const row =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const pinned = Array.isArray(row.pinnedApps)
    ? uniqueStrings(row.pinnedApps)
    : undefined;
  const folders = parseFolders(row.folders);
  const order = uniqueStrings(row.order);
  return {
    hiddenSystem: parseHiddenSystem(row.hiddenSystem),
    hiddenApps: uniqueStrings(row.hiddenApps),
    ...(pinned !== undefined ? { pinnedApps: pinned } : {}),
    ...(folders.length > 0 ? { folders } : {}),
    ...(order.length > 0 ? { order } : {}),
  };
}

export function loadDesktopOrg(): DesktopOrgPrefs {
  try {
    const raw = localStorage.getItem(DESKTOP_ORG_STORAGE_KEY);
    return raw ? sanitizeDesktopOrg(JSON.parse(raw)) : EMPTY_DESKTOP_ORG;
  } catch {
    return EMPTY_DESKTOP_ORG;
  }
}

export function saveDesktopOrg(prefs: DesktopOrgPrefs): void {
  try {
    localStorage.setItem(DESKTOP_ORG_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Private mode / quota: organization is a convenience, not correctness.
  }
}

export function hiddenSystemFor(
  prefs: DesktopOrgPrefs,
  agentId: string | null | undefined,
): Set<string> {
  if (!agentId) return new Set();
  return new Set(prefs.hiddenSystem[agentId] ?? []);
}

export function visibleSystemAppsFor(
  prefs: DesktopOrgPrefs,
  agentId: string | null | undefined,
  apps: readonly SystemAppDefinition[],
): SystemAppDefinition[] {
  const hidden = hiddenSystemFor(prefs, agentId);
  return apps.filter((app) => !hidden.has(app.key));
}

export function visibleAppsFor(
  prefs: DesktopOrgPrefs,
  apps: readonly AppSummary[],
): AppSummary[] {
  const hidden = new Set(prefs.hiddenApps);
  return apps.filter((app) => !hidden.has(app.app_id));
}

export function appEntryId(appId: string): string {
  return `app:${appId}`;
}

export function folderEntryId(folderId: string): string {
  return `folder:${folderId}`;
}

function folderEntry(
  folder: DesktopFolder,
  byId: ReadonlyMap<string, AppSummary>,
): DesktopEntry {
  return {
    kind: "folder",
    folder,
    apps: folder.appIds.flatMap((appId) => {
      const app = byId.get(appId);
      return app ? [app] : [];
    }),
  };
}

/**
 * Top-level desktop entries: ordered apps and custom folders.
 *
 * Hidden apps disappear from both the top level and folders. Empty folders
 * are not rendered; they remain in preferences only if malformed storage
 * produced them (sanitize drops those too).
 */
export function layoutDesktopEntries(
  prefs: DesktopOrgPrefs,
  apps: readonly AppSummary[],
): DesktopEntry[] {
  const visible = visibleAppsFor(prefs, apps);
  const byId = new Map(visible.map((app) => [app.app_id, app]));
  const folders = (prefs.folders ?? [])
    .map((folder) => ({
      ...folder,
      appIds: folder.appIds.filter((appId) => byId.has(appId)),
    }))
    .filter((folder) => folder.appIds.length > 0);
  const inFolder = new Set(folders.flatMap((folder) => folder.appIds));
  const topApps = visible.filter((app) => !inFolder.has(app.app_id));
  const topById = new Map(
    topApps.map((app) => [appEntryId(app.app_id), app] as const),
  );
  const folderById = new Map(
    folders.map((folder) => [folderEntryId(folder.id), folder] as const),
  );
  const entries: DesktopEntry[] = [];
  const seen = new Set<string>();

  for (const entryId of prefs.order ?? []) {
    if (seen.has(entryId)) continue;
    const app = topById.get(entryId);
    if (app) {
      entries.push({ kind: "app", app });
      seen.add(entryId);
      continue;
    }
    const folder = folderById.get(entryId);
    if (folder) {
      entries.push(folderEntry(folder, byId));
      seen.add(entryId);
    }
  }
  for (const app of topApps) {
    const entryId = appEntryId(app.app_id);
    if (seen.has(entryId)) continue;
    entries.push({ kind: "app", app });
    seen.add(entryId);
  }
  for (const folder of folders) {
    const entryId = folderEntryId(folder.id);
    if (seen.has(entryId)) continue;
    entries.push(folderEntry(folder, byId));
    seen.add(entryId);
  }
  return entries;
}

/**
 * Move a desktop entry before another one and persist the full order.
 *
 * ``allEntryIds`` is the caller's current layout order; it becomes the base
 * when the user has not reordered anything yet.
 */
export function reorderDesktopEntries(
  prefs: DesktopOrgPrefs,
  sourceEntryId: string,
  targetEntryId: string,
  allEntryIds: readonly string[],
): DesktopOrgPrefs {
  if (sourceEntryId === targetEntryId) return prefs;
  const known = new Set(allEntryIds);
  const order = [
    ...new Set((prefs.order ?? allEntryIds).filter((id) => known.has(id))),
  ];
  for (const id of allEntryIds) {
    if (!order.includes(id)) order.push(id);
  }
  const sourceIndex = order.indexOf(sourceEntryId);
  if (sourceIndex < 0) return prefs;
  order.splice(sourceIndex, 1);
  const targetIndex = order.indexOf(targetEntryId);
  if (targetIndex < 0) return prefs;
  order.splice(targetIndex, 0, sourceEntryId);
  return { ...prefs, order };
}

let folderSequence = 0;

function newFolderId(): string {
  folderSequence += 1;
  return `folder_${Date.now().toString(36)}_${folderSequence.toString(36)}`;
}

/**
 * Create a folder from the selected apps.
 *
 * Selected apps leave any previous folder and their top-level slots; the new
 * folder is appended to the desktop order. ``knownEntryIds`` supplies the
 * pre-folder top-level order when the user has never reordered anything.
 */
export function createDesktopFolder(
  prefs: DesktopOrgPrefs,
  name: string,
  appIds: readonly string[],
  knownEntryIds: readonly string[],
  id: string = newFolderId(),
): DesktopOrgPrefs {
  const cleanName = name.trim().slice(0, 40);
  const selected = [...new Set(appIds)];
  if (!cleanName || selected.length === 0) return prefs;
  const selectedSet = new Set(selected);
  const folders = (prefs.folders ?? [])
    .map((folder) => ({
      ...folder,
      appIds: folder.appIds.filter((appId) => !selectedSet.has(appId)),
    }))
    .filter((folder) => folder.appIds.length > 0);
  if (folders.some((folder) => folder.id === id)) return prefs;
  folders.push({ id, name: cleanName, appIds: selected });
  const order = (prefs.order ?? knownEntryIds).filter((entryId) => {
    if (!entryId.startsWith("app:")) return true;
    return !selectedSet.has(entryId.slice(4));
  });
  order.push(folderEntryId(id));
  return { ...prefs, folders, order };
}

/**
 * Rename a folder or change its apps.
 *
 * Apps moved into the folder leave the top level and any previous folder;
 * folders left empty by the move are removed from the desktop and order.
 */
export function updateDesktopFolder(
  prefs: DesktopOrgPrefs,
  folderId: string,
  name: string,
  appIds: readonly string[],
  knownEntryIds: readonly string[],
): DesktopOrgPrefs {
  const cleanName = name.trim().slice(0, 40);
  const selected = [...new Set(appIds)];
  if (!cleanName || selected.length === 0) return prefs;
  if (!(prefs.folders ?? []).some((folder) => folder.id === folderId)) {
    return prefs;
  }
  const selectedSet = new Set(selected);
  const removedFolders = new Set<string>();
  const folders: DesktopFolder[] = [];
  for (const folder of prefs.folders ?? []) {
    if (folder.id === folderId) continue;
    const remaining = folder.appIds.filter((appId) => !selectedSet.has(appId));
    if (remaining.length > 0) folders.push({ ...folder, appIds: remaining });
    else removedFolders.add(folder.id);
  }
  folders.push({ id: folderId, name: cleanName, appIds: selected });
  const order = (prefs.order ?? knownEntryIds).filter((entryId) => {
    if (entryId.startsWith("app:")) {
      return !selectedSet.has(entryId.slice(4));
    }
    if (entryId.startsWith("folder:")) {
      return !removedFolders.has(entryId.slice(7));
    }
    return true;
  });
  if (!order.includes(folderEntryId(folderId))) {
    order.push(folderEntryId(folderId));
  }
  return { ...prefs, folders, order };
}

export function deleteDesktopFolder(
  prefs: DesktopOrgPrefs,
  folderId: string,
): DesktopOrgPrefs {
  const folders = (prefs.folders ?? []).filter(
    (folder) => folder.id !== folderId,
  );
  const removedId = folderEntryId(folderId);
  const order = prefs.order?.filter((entryId) => entryId !== removedId);
  const next: DesktopOrgPrefs = { ...prefs };
  if (folders.length > 0) next.folders = folders;
  else delete next.folders;
  if (order && order.length > 0) next.order = order;
  else delete next.order;
  return next;
}

/**
 * Apps pinned to the dock.
 *
 * Before the user customizes the dock, keep the legacy behavior (first N
 * alphabetically sorted apps). Once ``pinnedApps`` is set, only those ids
 * appear, in the user's chosen order.
 */
export function dockAppsFor(
  prefs: DesktopOrgPrefs,
  apps: readonly AppSummary[],
  limit: number,
): AppSummary[] {
  const visible = visibleAppsFor(prefs, apps);
  if (prefs.pinnedApps === undefined) return visible.slice(0, limit);
  const byId = new Map(visible.map((app) => [app.app_id, app]));
  return prefs.pinnedApps
    .map((id) => byId.get(id))
    .filter((app): app is AppSummary => Boolean(app))
    .slice(0, limit);
}

export function setSystemAppHidden(
  prefs: DesktopOrgPrefs,
  agentId: string,
  key: string,
  hidden: boolean,
): DesktopOrgPrefs {
  const current = new Set(prefs.hiddenSystem[agentId] ?? []);
  if (hidden) current.add(key);
  else current.delete(key);
  const next = { ...prefs.hiddenSystem };
  if (current.size > 0) next[agentId] = [...current];
  else delete next[agentId];
  return { ...prefs, hiddenSystem: next };
}

export function setAppHidden(
  prefs: DesktopOrgPrefs,
  appId: string,
  hidden: boolean,
): DesktopOrgPrefs {
  const current = new Set(prefs.hiddenApps);
  if (hidden) current.add(appId);
  else current.delete(appId);
  const pinnedApps =
    hidden && prefs.pinnedApps
      ? prefs.pinnedApps.filter((id) => id !== appId)
      : prefs.pinnedApps;
  const next: DesktopOrgPrefs = {
    ...prefs,
    hiddenApps: [...current],
  };
  if (pinnedApps !== undefined) next.pinnedApps = pinnedApps;
  if (hidden && prefs.folders) {
    const folders = prefs.folders
      .map((folder) => ({
        ...folder,
        appIds: folder.appIds.filter((id) => id !== appId),
      }))
      .filter((folder) => folder.appIds.length > 0);
    const keptFolders = new Set(folders.map((folder) => folder.id));
    if (folders.length > 0) next.folders = folders;
    else delete next.folders;
    if (prefs.order) {
      const order = prefs.order.filter((entryId) => {
        if (entryId === appEntryId(appId)) return false;
        if (entryId.startsWith("folder:")) {
          return keptFolders.has(entryId.slice(7));
        }
        return true;
      });
      if (order.length > 0) next.order = order;
      else delete next.order;
    }
  }
  return next;
}

export function setAppPinned(
  prefs: DesktopOrgPrefs,
  appId: string,
  pinned: boolean,
  defaultPinnedIds: readonly string[],
): DesktopOrgPrefs {
  const base = prefs.pinnedApps ?? [...defaultPinnedIds];
  const current = new Set(base);
  if (pinned) current.add(appId);
  else current.delete(appId);
  return { ...prefs, pinnedApps: [...current] };
}

export function resetDesktopOrg(): DesktopOrgPrefs {
  return { hiddenSystem: {}, hiddenApps: [] };
}
