import { request } from "../../../api/request";

export interface SystemActionContext {
  agentId: string;
}

export type SystemActionParams = Record<string, unknown>;

const SYSTEM_ACTION_HANDLERS: Record<
  string,
  (ctx: SystemActionContext, params: SystemActionParams) => Promise<unknown>
> = {
  "skills.toggle": async (ctx, params) => {
    const slug = String(params.slug ?? "").trim();
    if (!slug) throw new Error("skill slug is required");
    const action = params.enabled === true ? "enable" : "disable";
    await request(
      `/agents/${encodeURIComponent(ctx.agentId)}/skills/${encodeURIComponent(
        slug,
      )}/${action}`,
      { method: "POST" },
    );
    return { ok: true, enabled: action === "enable" };
  },
  "cron.run": async (ctx, params) => {
    const cronId = String(params.cron_id ?? "").trim();
    if (!cronId) throw new Error("cron id is required");
    await request(
      `/agents/${encodeURIComponent(ctx.agentId)}/cron/${encodeURIComponent(
        cronId,
      )}/run-now`,
      { method: "POST" },
    );
    return { ok: true };
  },
};

export async function runSystemAction(
  key: string,
  params: SystemActionParams,
  ctx: SystemActionContext,
): Promise<unknown> {
  const handler = SYSTEM_ACTION_HANDLERS[key];
  if (!handler) throw new Error(`unknown system action: ${key}`);
  return handler(ctx, params);
}

export { SYSTEM_ACTION_HANDLERS };
