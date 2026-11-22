import { getApiUrl } from "./config";
import i18n from "../i18n";
import { markNavigatingAway } from "../utils/reloadOnStaleChunk";

const AUTH_TOKEN_KEY = "auth_token";
/** sessionStorage handoff for SSO redirects / popups (default = remember). */
const AUTH_REMEMBER_PREF_KEY = "auth_remember_pref";
/** MiniApp-scoped session marker; cleared whenever a normal token is set. */
const APP_SCOPE_KEY = "octop_app_scope";
/** Per-app scoped token, keyed by app id and held for this tab only. */
const APP_TOKEN_PREFIX = "app_token:";
/** Guest portal session (7d token + 30d rotating refresh token). */
const GUEST_TOKEN_KEY = "guest_token";
const GUEST_REFRESH_KEY = "guest_refresh_token";
const GUEST_EXPIRES_KEY = "guest_token_expires_at";
const GUEST_REFRESH_EXPIRES_KEY = "guest_refresh_expires_at";

const APP_ROUTE_SEGMENTS = new Set([
  "access-requests",
  "boards",
  "dataset",
  "from-template",
  "in-thread",
  "invites",
  "join",
  "quick",
  "restore",
  "setup-plans",
  "templates",
]);

export type RequestAuthKind = "platform" | "app" | "guest" | "public" | "none";

export interface RequestAuth {
  kind: RequestAuthKind;
  token: string;
  appId?: string;
}

export interface GuestSessionInput {
  guestToken: string;
  refreshToken?: string | null;
  guestExpiresIn?: number;
  refreshExpiresIn?: number;
  remember?: boolean;
}

export interface AppScopeInfo {
  app_id: string;
  /** Unix timestamp when the scoped token expires. */
  expires_at: number;
}

/** Remember that this tab holds an app-scoped token (one MiniApp only). */
export function setAppScope(info: AppScopeInfo | null): void {
  if (info) {
    sessionStorage.setItem(APP_SCOPE_KEY, JSON.stringify(info));
  } else {
    sessionStorage.removeItem(APP_SCOPE_KEY);
  }
}

/** Read the current app-scoped session marker, if any. */
export function getAppScope(): AppScopeInfo | null {
  const raw = sessionStorage.getItem(APP_SCOPE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<AppScopeInfo>;
    if (
      typeof parsed.app_id !== "string" ||
      typeof parsed.expires_at !== "number"
    ) {
      return null;
    }
    return { app_id: parsed.app_id, expires_at: parsed.expires_at };
  } catch {
    return null;
  }
}

/**
 * Fired when the session is no longer valid. Cancelable: a listener inside the
 * React tree calls ``preventDefault()`` to route to /login itself, which keeps
 * the SPA alive instead of tearing the document down mid-render.
 */
export const UNAUTHORIZED_EVENT = "octop:unauthorized";
/** Fired when an agent-scoped API request is forbidden. */
export const FORBIDDEN_EVENT = "octop:forbidden";

/** Response header used by the server for JWT sliding renewal. */
export const ACCESS_TOKEN_RESPONSE_HEADER = "X-Octop-Access-Token";

/** Quiet error thrown when setup lockdown blocks a non-wizard API call. */
export class SetupRequiredError extends Error {
  constructor() {
    super("Setup required");
    this.name = "SetupRequiredError";
  }
}

/** Session flag: backend reported no admin yet (setup lockdown active). */
let _setupRequiredKnown = false;

/** Mark that first-run setup is still required (skips further locked APIs). */
export function markSetupRequired(): void {
  _setupRequiredKnown = true;
}

/** Clear the setup-lockdown short-circuit (after admin exists / login). */
export function clearSetupRequired(): void {
  _setupRequiredKnown = false;
}

/** Whether this tab already knows setup lockdown is active. */
export function isSetupRequiredKnown(): boolean {
  return _setupRequiredKnown;
}

/** Stash the login-page "remember me" choice for SSO redirects / popups. */
export function setRememberLoginPreference(remember: boolean): void {
  sessionStorage.setItem(AUTH_REMEMBER_PREF_KEY, remember ? "1" : "0");
}

/** Read the stashed remember preference (defaults to true). */
export function getRememberLoginPreference(): boolean {
  return sessionStorage.getItem(AUTH_REMEMBER_PREF_KEY) !== "0";
}

/**
 * Whether the current token is session-only (not "remember me").
 * Session-only tokens live in ``sessionStorage`` so a new tab does not
 * share — or erase — another tab's login.
 */
export function isSessionOnlyAuth(): boolean {
  return (
    sessionStorage.getItem(AUTH_TOKEN_KEY) != null &&
    localStorage.getItem(AUTH_TOKEN_KEY) == null
  );
}

/**
 * Save JWT token. When ``remember`` is false the token is kept in
 * ``sessionStorage`` (cleared when the tab/window closes). SSO popups must
 * post the token back to the opener — ``sessionStorage`` is not shared.
 */
export function setAuthToken(token: string, remember: boolean = true) {
  if (remember) {
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
  } else {
    sessionStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.removeItem(AUTH_TOKEN_KEY);
  }
  // A normal sign-in replaces any previous MiniApp-scoped session marker;
  // ``AppAccessComplete`` sets it again right after exchanging the code.
  setAppScope(null);
  clearSetupRequired();
}

/** Get JWT token from localStorage or (session-only) sessionStorage. */
export function getAuthToken(): string {
  return (
    localStorage.getItem(AUTH_TOKEN_KEY) ||
    sessionStorage.getItem(AUTH_TOKEN_KEY) ||
    ""
  );
}

function readTokenKey(key: string): string {
  return localStorage.getItem(key) || sessionStorage.getItem(key) || "";
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

/** Persist a guest session; ``remember`` chooses localStorage vs sessionStorage. */
export function setGuestSession(input: GuestSessionInput): void {
  clearGuestSession();
  const store = input.remember === false ? sessionStorage : localStorage;
  store.setItem(GUEST_TOKEN_KEY, input.guestToken);
  if (input.refreshToken) {
    store.setItem(GUEST_REFRESH_KEY, input.refreshToken);
  }
  store.setItem(
    GUEST_EXPIRES_KEY,
    String(nowSeconds() + (input.guestExpiresIn ?? 7 * 86400)),
  );
  store.setItem(
    GUEST_REFRESH_EXPIRES_KEY,
    String(nowSeconds() + (input.refreshExpiresIn ?? 30 * 86400)),
  );
}

/** Read the guest token, dropping it locally once it has expired. */
export function getGuestToken(): string {
  const expiry = readTokenKey(GUEST_EXPIRES_KEY);
  if (expiry && Number(expiry) <= nowSeconds()) return "";
  return readTokenKey(GUEST_TOKEN_KEY);
}

/** Read the opaque refresh token (still useful after the 7-day token expires). */
export function getGuestRefreshToken(): string {
  const expiry = readTokenKey(GUEST_REFRESH_EXPIRES_KEY);
  if (expiry && Number(expiry) <= nowSeconds()) return "";
  return readTokenKey(GUEST_REFRESH_KEY);
}

export function hasGuestSession(): boolean {
  return Boolean(getGuestToken() || getGuestRefreshToken());
}

/** Whether the guest session was stored with "remember me" (localStorage). */
export function isGuestSessionRemembered(): boolean {
  return (
    localStorage.getItem(GUEST_TOKEN_KEY) != null ||
    localStorage.getItem(GUEST_REFRESH_KEY) != null
  );
}

export function clearGuestSession(): void {
  for (const store of [localStorage, sessionStorage]) {
    store.removeItem(GUEST_TOKEN_KEY);
    store.removeItem(GUEST_REFRESH_KEY);
    store.removeItem(GUEST_EXPIRES_KEY);
    store.removeItem(GUEST_REFRESH_EXPIRES_KEY);
  }
}

interface StoredAppToken {
  token: string;
  expires_at: number;
}

/** Keep a short-lived app token for one tab and one app id. */
export function setAppToken(
  appId: string,
  token: string,
  expiresIn = 7200,
): void {
  const value: StoredAppToken = {
    token,
    expires_at: nowSeconds() + expiresIn,
  };
  sessionStorage.setItem(APP_TOKEN_PREFIX + appId, JSON.stringify(value));
}

/** Read an app token; an expired or malformed value is discarded. */
export function getAppToken(appId: string): string {
  const raw = sessionStorage.getItem(APP_TOKEN_PREFIX + appId);
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw) as Partial<StoredAppToken>;
    if (
      typeof parsed.token !== "string" ||
      typeof parsed.expires_at !== "number" ||
      parsed.expires_at <= nowSeconds()
    ) {
      sessionStorage.removeItem(APP_TOKEN_PREFIX + appId);
      return "";
    }
    return parsed.token;
  } catch {
    sessionStorage.removeItem(APP_TOKEN_PREFIX + appId);
    return "";
  }
}

export function clearAppToken(appId: string): void {
  sessionStorage.removeItem(APP_TOKEN_PREFIX + appId);
}

/** Drop every per-app token in this tab (guest logout / account deletion). */
export function clearAllAppTokens(): void {
  const keys: string[] = [];
  for (let i = 0; i < sessionStorage.length; i += 1) {
    const key = sessionStorage.key(i);
    if (key?.startsWith(APP_TOKEN_PREFIX)) keys.push(key);
  }
  for (const key of keys) sessionStorage.removeItem(key);
}

function appResourceId(path: string): string | undefined {
  const clean = path.split("?")[0];
  const match = /^\/apps\/([^/]+)/.exec(clean);
  if (!match) return undefined;
  const segment = match[1];
  if (APP_ROUTE_SEGMENTS.has(segment)) return undefined;
  return segment;
}

/**
 * Pick the narrowest credential for a request path.
 *
 * App routes prefer the per-app token, then the normal login (a platform
 * member can call them until the app session is minted), then a guest token.
 * Public invite / join redemption reuses a guest identity when present.
 */
export function resolveRequestAuth(path: string): RequestAuth {
  const clean = path.split("?")[0];
  if (clean.startsWith("/guest/")) {
    return { kind: "guest", token: getGuestToken() };
  }
  if (/^\/apps\/(invites|join)\/(preview|redeem)$/.test(clean)) {
    const auth = getAuthToken();
    if (auth) return { kind: "platform", token: auth };
    const guest = getGuestToken();
    return { kind: "guest", token: guest };
  }
  const appId = appResourceId(clean);
  if (appId) {
    const appToken = getAppToken(appId);
    if (appToken) return { kind: "app", token: appToken, appId };
    const auth = getAuthToken();
    if (auth) return { kind: "platform", token: auth, appId };
    // Guest tokens are only valid on `/guest/*`; the runtime mints an
    // app-scoped token first.
    return { kind: "none", token: "", appId };
  }
  const auth = getAuthToken();
  return auth ? { kind: "platform", token: auth } : { kind: "none", token: "" };
}

/** Remove JWT token from both storages. */
export function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  sessionStorage.removeItem(AUTH_REMEMBER_PREF_KEY);
  sessionStorage.removeItem(APP_SCOPE_KEY);
  localStorage.removeItem("octop:active-agent");
  setActiveAgentId(null);
  clearGuestSession();
  clearAllAppTokens();
}

/** Persist a sliding-renewed access token from an API response, if present. */
export function applyRenewedAccessToken(response: Response): void {
  const renewed = response.headers.get(ACCESS_TOKEN_RESPONSE_HEADER);
  if (renewed) {
    setAuthToken(renewed, !isSessionOnlyAuth());
  }
}

let _redirectingToSetup = false;

/**
 * Hard-redirect once when the backend reports the wizard isn't done.
 * The flag prevents N parallel API calls from each issuing a navigate.
 */
function handleSetupRequired(): void {
  markSetupRequired();
  if (_redirectingToSetup) return;
  // Already on a public bootstrap route — a 503 from a background prefetch
  // must not reload the page or we loop forever.
  const path = window.location.pathname;
  if (
    path.startsWith("/setup") ||
    path.startsWith("/login") ||
    path.startsWith("/invite")
  ) {
    return;
  }
  _redirectingToSetup = true;
  // Full reload drops any in-flight React state.
  window.location.replace("/setup");
}

/** Wizard endpoints stay reachable while lockdown is active. */
function isSetupApiPath(path: string): boolean {
  return path === "/setup/status" || path.startsWith("/setup/");
}

/**
 * Skip the network when this tab already knows setup lockdown is on.
 * Avoids stampeding ``/api/agents`` etc. after the first 503 / status probe.
 */
function assertNotSetupLocked(path: string): void {
  if (_setupRequiredKnown && !isSetupApiPath(path)) {
    handleSetupRequired();
    throw new SetupRequiredError();
  }
}

/**
 * Inspect a response for the lockdown signal (503 + body
 * `{setup_required: true}`) and trigger a one-shot navigate to /setup.
 *
 * Returns ``true`` when the response matched and the caller should
 * abort the normal success/error path. The wizard's own ``/setup/*``
 * calls are exempt to avoid redirect loops.
 */
async function check503ForSetupRequired(
  path: string,
  response: Response,
): Promise<boolean> {
  if (response.status !== 503) return false;
  let body: unknown = null;
  try {
    body = await response.clone().json();
  } catch {
    /* not JSON — fall through to the standard error path. */
    return false;
  }
  if (
    body &&
    typeof body === "object" &&
    (body as Record<string, unknown>).setup_required === true
  ) {
    markSetupRequired();
    clearAuthToken();
    if (!isSetupApiPath(path)) {
      handleSetupRequired();
    }
    return true;
  }
  return false;
}

/**
 * Active agent id — populated by ``AgentProvider`` in ``context/AgentContext.tsx``
 * whenever the user picks a new agent in the top-bar switcher. Stored at
 * module scope so plain functions like ``request()`` can read it without
 * threading a context through every call site.
 *
 * The value is ALSO mirrored to ``localStorage["octop:active-agent"]`` by
 * the provider — but the source of truth at request time is this variable
 * so reactions stay synchronous.
 */
let activeAgentId: string | null = null;

/** Setter used by AgentProvider; also clears when ``null``. */
export function setActiveAgentId(id: string | null) {
  activeAgentId = id;
}

/** Read the active agent id (e.g. from non-React code). */
export function getActiveAgentId(): string | null {
  return activeAgentId;
}

/**
 * Optional ``X-Octop-Agent-Id`` for caller-opted header tunnels (composer
 * GETs, connector lists). Do not auto-attach these on settings pages.
 */
export function bridgeAgentHeaders(
  agentId?: string | null,
): Record<string, string> | undefined {
  const id = (agentId ?? "").trim();
  if (!id) return undefined;
  return { "X-Octop-Agent-Id": id };
}

/**
 * Decide whether a request path is "agent-scoped" — i.e. talking to a
 * concrete agent's resource — and therefore should carry the
 * ``X-Octop-Agent-Id`` header. Health, admin, auth, setup, providers, and
 * personas don't need it.
 *
 * Composer / knowledge / browser host GETs are NOT auto-scoped: those
 * settings pages must keep talking to this instance. Callers that need a
 * remote hop pass ``bridgeAgentHeaders(agentId)`` explicitly.
 */
function isAgentScopedPath(path: string): boolean {
  // Match `/agents/<id>/...` (one trailing segment after the id).
  // The path passed to request() is stripped of the /api prefix.
  if (/^\/agents\/[^/]+(\/|$)/.test(path)) return true;
  // MBTI endpoints that read/write the active agent's persona config.
  if (/^\/mbti\//.test(path)) return true;
  if (path === "/cron/settings") return true;
  if (path === "/subagent-catalog" || path.startsWith("/subagent-catalog/")) {
    return true;
  }
  return false;
}

function buildHeaders(path: string, extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Accept-Language": i18n.language?.startsWith("zh") ? "zh" : "en",
  };

  // Apply the global JWT first; the caller's `extra` (including a
  // wizard token) can still override it below.
  const auth = resolveRequestAuth(path);
  if (auth.token) {
    headers.Authorization = `Bearer ${auth.token}`;
  }

  // Caller-supplied headers win — needed so the setup wizard can pass
  // its short-TTL Bearer without being stomped by a stale localStorage
  // JWT.
  if (extra) {
    const extraEntries =
      extra instanceof Headers
        ? Array.from(extra.entries())
        : Array.isArray(extra)
        ? extra
        : Object.entries(extra);
    for (const [k, v] of extraEntries) {
      headers[k] = String(v);
    }
  }

  if (
    activeAgentId &&
    isAgentScopedPath(path) &&
    !headers["X-Octop-Agent-Id"]
  ) {
    headers["X-Octop-Agent-Id"] = activeAgentId;
  }

  return headers;
}

/**
 * Build auth-only headers (no Content-Type — let the browser set it for FormData).
 */
function buildAuthHeaders(path: string): Record<string, string> {
  const headers: Record<string, string> = {
    "Accept-Language": i18n.language?.startsWith("zh") ? "zh" : "en",
  };
  const auth = resolveRequestAuth(path);
  if (auth.token) {
    headers.Authorization = `Bearer ${auth.token}`;
  }
  if (
    activeAgentId &&
    isAgentScopedPath(path) &&
    !headers["X-Octop-Agent-Id"]
  ) {
    headers["X-Octop-Agent-Id"] = activeAgentId;
  }
  return headers;
}

let _redirectingToLogin = false;

/** 401 codes that mean this Octop session is no longer valid. */
const SESSION_UNAUTHORIZED_CODES = new Set(["AUTH_FAILED", "TOKEN_EXPIRED"]);

/**
 * Send the user to the login screen exactly once, no matter how many parallel
 * requests report an expired session.
 */
function handleUnauthorized(): void {
  if (_redirectingToLogin) return;
  const path = window.location.pathname;
  if (
    path.startsWith("/setup") ||
    path.startsWith("/login") ||
    path.startsWith("/invite")
  )
    return;
  _redirectingToLogin = true;

  const takenOver = !window.dispatchEvent(
    new CustomEvent(UNAUTHORIZED_EVENT, { cancelable: true }),
  );
  if (takenOver) return;

  markNavigatingAway();
  window.location.replace("/login");
}

/**
 * Handle 401 responses: clear token, redirect to login, and throw.
 * Shared by request(), requestBlob(), and requestUpload().
 */
async function errorCodeOf(response: Response): Promise<string | undefined> {
  try {
    const body = (await response.clone().json()) as {
      error?: { code?: string };
    };
    const code = body?.error?.code;
    return typeof code === "string" && code.trim() ? code : undefined;
  } catch {
    return undefined;
  }
}

async function throwIfUnauthorized(
  path: string,
  response: Response,
): Promise<void> {
  if (response.status !== 401 || path.startsWith("/auth/")) {
    return;
  }
  const auth = resolveRequestAuth(path);
  if (auth.kind === "app" || auth.kind === "guest") {
    // A scoped session expiring is not a dead dashboard login. Let the app /
    // guest portal refresh or ask for a new join link instead of clearing the
    // platform token and bouncing to /login.
    if (auth.kind === "app" && auth.appId) clearAppToken(auth.appId);
    const text = await response.text().catch(() => "");
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }
  const code = await errorCodeOf(response);
  // Business 401s (peer login, etc.) are not a dead local session.
  if (
    code &&
    !SESSION_UNAUTHORIZED_CODES.has(code) &&
    !path.startsWith("/setup/")
  ) {
    return;
  }
  clearAuthToken();
  handleUnauthorized();
  let message = "Unauthorized";
  if (path.startsWith("/setup/")) {
    try {
      const body = (await response.clone().json()) as {
        error?: { message?: string };
      };
      if (body?.error?.message) {
        message = body.error.message;
      }
    } catch {
      /* keep generic message */
    }
  }
  throw new Error(message);
}

export async function request<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  assertNotSetupLocked(path);

  const url = getApiUrl(path);

  const headers = buildHeaders(path, options.headers);

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (await check503ForSetupRequired(path, response)) {
    throw new SetupRequiredError();
  }

  await throwIfUnauthorized(path, response);
  applyRenewedAccessToken(response);

  if (!response.ok) {
    if (response.status === 403 && isAgentScopedPath(path)) {
      window.dispatchEvent(
        new CustomEvent(FORBIDDEN_EVENT, { detail: { path } }),
      );
    }
    const text = await response.text().catch(() => "");
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return (await response.text()) as unknown as T;
  }

  return (await response.json()) as T;
}

/**
 * Download a binary resource as a Blob.
 *
 * ``onProgress`` (when the server sends ``Content-Length``) streams the body
 * and reports ``(loaded, total)`` in bytes — used by the PDF preview's
 * download progress bar.
 */
export async function requestBlob(
  path: string,
  options: RequestInit = {},
  onProgress?: (loaded: number, total: number) => void,
): Promise<Blob> {
  assertNotSetupLocked(path);

  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });

  if (await check503ForSetupRequired(path, response)) {
    throw new SetupRequiredError();
  }

  await throwIfUnauthorized(path, response);
  applyRenewedAccessToken(response);

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  const total = Number(response.headers.get("content-length")) || 0;
  if (onProgress && total > 0 && response.body) {
    const reader = response.body.getReader();
    const chunks: BlobPart[] = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value as unknown as BlobPart);
        loaded += value.byteLength;
        onProgress(loaded, total);
      }
    }
    return new Blob(chunks);
  }

  return response.blob();
}

/**
 * Authenticated GET that only checks success — cancels the body without
 * buffering it (existence probes for large workspace files).
 */
export async function probeAuthResource(
  path: string,
  options: RequestInit = {},
): Promise<void> {
  assertNotSetupLocked(path);

  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });

  if (await check503ForSetupRequired(path, response)) {
    throw new SetupRequiredError();
  }

  await throwIfUnauthorized(path, response);
  applyRenewedAccessToken(response);

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  try {
    await response.body?.cancel();
  } catch {
    /* ignore cancel failures */
  }
}

/**
 * POST JSON and hand back the response body as a byte stream (chunked TTS).
 * Mirrors requestBlob()'s auth/setup/401 handling but never buffers.
 */
export async function requestStream(
  path: string,
  options: RequestInit = {},
): Promise<{ contentType: string; body: ReadableStream<Uint8Array> }> {
  assertNotSetupLocked(path);

  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });

  if (await check503ForSetupRequired(path, response)) {
    throw new SetupRequiredError();
  }

  await throwIfUnauthorized(path, response);
  applyRenewedAccessToken(response);

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  if (!response.body) {
    throw new Error("Empty stream from server");
  }

  return {
    contentType: response.headers.get("content-type") || "",
    body: response.body,
  };
}

export type UploadProgressHandler = (percent: number) => void;

/**
 * Upload a FormData payload (no explicit Content-Type — browser handles boundary).
 * Uses XMLHttpRequest so callers can report upload progress.
 */
export async function requestUpload<T = unknown>(
  path: string,
  body: FormData,
  options: RequestInit = {},
  onProgress?: UploadProgressHandler,
): Promise<T> {
  assertNotSetupLocked(path);

  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const method = options.method ?? "POST";

  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);

    for (const [key, value] of Object.entries(headers)) {
      xhr.setRequestHeader(key, value);
    }
    if (options.headers) {
      const extraEntries =
        options.headers instanceof Headers
          ? Array.from(options.headers.entries())
          : Array.isArray(options.headers)
          ? options.headers
          : Object.entries(options.headers);
      for (const [k, v] of extraEntries) {
        xhr.setRequestHeader(k, String(v));
      }
    }

    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
    }

    if (options.signal) {
      if (options.signal.aborted) {
        reject(new DOMException("The operation was aborted.", "AbortError"));
        return;
      }
      options.signal.addEventListener(
        "abort",
        () => {
          xhr.abort();
        },
        { once: true },
      );
    }

    xhr.onload = () => {
      void (async () => {
        const status = xhr.status;
        const responseText = xhr.responseText;
        const responseHeaders = new Headers();
        const renewed = xhr.getResponseHeader(ACCESS_TOKEN_RESPONSE_HEADER);
        if (renewed) {
          responseHeaders.set(ACCESS_TOKEN_RESPONSE_HEADER, renewed);
        }
        const response = new Response(responseText, {
          status,
          headers: responseHeaders,
        });

        if (await check503ForSetupRequired(path, response)) {
          reject(new SetupRequiredError());
          return;
        }

        try {
          await throwIfUnauthorized(path, response);
        } catch (err) {
          reject(err);
          return;
        }

        applyRenewedAccessToken(response);

        if (!response.ok) {
          // Same shape as request() so parseApiError() can read the error envelope.
          reject(
            new Error(
              `Upload failed: ${status}${
                responseText ? ` - ${responseText}` : ""
              }`,
            ),
          );
          return;
        }

        try {
          resolve((await response.json()) as T);
        } catch {
          reject(new Error("Upload failed: invalid JSON response"));
        }
      })();
    };

    xhr.onerror = () => reject(new Error("Network error"));
    xhr.onabort = () =>
      reject(new DOMException("The operation was aborted.", "AbortError"));

    xhr.send(body);
  });
}
