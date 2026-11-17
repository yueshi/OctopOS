import { request } from "../../../api/request";
import type { SystemQueryContext, SystemQueryParams } from "./types";

type JsonRecord = Record<string, unknown>;

const WINDOW_PATTERN =
  /^(today|yesterday|last_7d|last_30d|all|day:\d{4}-\d{2}-\d{2}|month:\d{4}-\d{2}|range:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2})$/;
const GRANULARITIES = new Set(["total", "by_day", "by_agent", "by_model"]);

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function asRows(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is JsonRecord =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function bool(value: unknown): boolean {
  return value === true;
}

function safeWindow(value: unknown): string {
  const text = typeof value === "string" ? value : "";
  return WINDOW_PATTERN.test(text) ? text : "last_30d";
}

function safeGranularity(value: unknown): string {
  const text = typeof value === "string" ? value : "";
  return GRANULARITIES.has(text) ? text : "by_day";
}

function projectUsageSummary(payload: unknown): JsonRecord {
  const data = asRecord(payload);
  return {
    window: str(data.window),
    granularity: str(data.granularity),
    range_start: num(data.range_start),
    range_end: num(data.range_end),
    input_tokens: num(data.input_tokens),
    uncached_input_tokens: num(data.uncached_input_tokens),
    cache_read_tokens: num(data.cache_read_tokens),
    cache_write_tokens: num(data.cache_write_tokens),
    output_tokens: num(data.output_tokens),
    reasoning_tokens: num(data.reasoning_tokens),
    total_tokens: num(data.total_tokens),
    model_calls: num(data.model_calls),
    cache_hit_percent: num(data.cache_hit_percent),
    turns: num(data.turns),
    avg_per_turn: num(data.avg_per_turn),
    buckets: asRows(data.buckets).map((bucket) => ({
      key: str(bucket.key),
      label: str(bucket.label) || str(bucket.key),
      input_tokens: num(bucket.input_tokens),
      output_tokens: num(bucket.output_tokens),
      cache_read_tokens: num(bucket.cache_read_tokens),
      total_tokens: num(bucket.total_tokens),
      model_calls: num(bucket.model_calls),
      turns: num(bucket.turns),
    })),
  };
}

function projectAgentMetrics(payload: unknown): JsonRecord {
  const data = asRecord(payload);
  const tasks = asRecord(data.tasks);
  const usage = asRecord(data.usage);
  const budget = asRecord(data.budget);
  return {
    agent_id: str(data.agent_id),
    window: str(data.window),
    tasks: Object.fromEntries(
      Object.entries(tasks).map(([key, value]) => [key, num(value)]),
    ),
    usage: {
      input_tokens: num(usage.input_tokens),
      output_tokens: num(usage.output_tokens),
      total_tokens: num(usage.total_tokens),
      model_calls: num(usage.model_calls),
    },
    budget: {
      used: num(budget.used),
      limit: num(budget.limit),
      remaining: num(budget.remaining),
    },
  };
}

function projectAgentInbox(payload: unknown): JsonRecord {
  const data = asRecord(payload);
  const counts = asRecord(data.counts);
  return {
    agent_id: str(data.agent_id),
    counts: Object.fromEntries(
      Object.entries(counts).map(([key, value]) => [key, num(value)]),
    ),
    items: asRows(data.items).map((item) => ({
      kind: str(item.kind),
      task_id: str(item.task_id),
      title: str(item.title),
      detail: str(item.detail).slice(0, 500),
      since: typeof item.since === "number" ? item.since : null,
    })),
  };
}

function projectAgentStatus(payload: unknown): JsonRecord {
  const data = asRecord(payload);
  const maintenance = asRecord(data.memory_maintenance);
  return {
    state: str(data.state) || "unknown",
    last_error: str(data.last_error).slice(0, 500),
    channels: asRows(data.channels).map((channel) => ({
      id: str(channel.id),
      kind: str(channel.kind),
      name: str(channel.name),
    })),
    cron_jobs: asRows(data.cron_jobs).map((job) => ({
      id: str(job.id),
      prompt: str(job.prompt).slice(0, 200),
      trigger: str(job.trigger),
    })),
    memory_maintenance: {
      kind: str(maintenance.kind),
      busy: bool(maintenance.busy),
    },
  };
}

function projectChannels(payload: unknown): JsonRecord {
  return {
    channels: asRows(payload).map((channel) => {
      const runtime = asRecord(channel.runtime);
      return {
        id: str(channel.id),
        agent_id: str(channel.agent_id),
        kind: str(channel.kind),
        name: str(channel.name),
        enabled: bool(channel.enabled),
        connected: bool(runtime.connected),
        error: str(runtime.error).slice(0, 500),
        updated_at:
          typeof runtime.updated_at === "number" ? runtime.updated_at : null,
      };
    }),
  };
}

function projectSkills(payload: unknown): JsonRecord {
  return {
    skills: asRows(payload).map((skill) => ({
      slug: str(skill.slug) || str(skill.name),
      name: str(skill.name) || str(skill.label) || str(skill.slug),
      description: str(skill.description).slice(0, 300),
      enabled: skill.enabled !== false,
      kind: str(skill.kind) || "workspace",
    })),
  };
}

function projectConnectors(payload: unknown): JsonRecord {
  return {
    connectors: asRows(payload).map((connector) => ({
      instance_id: str(connector.instance_id),
      kind: str(connector.kind),
      display_name: str(connector.display_name),
      description: str(connector.description).slice(0, 300),
      status: str(connector.status),
      has_credentials: bool(connector.has_credentials),
      shared: bool(connector.shared),
      can_manage: bool(connector.can_manage),
    })),
  };
}

function projectTools(payload: unknown): JsonRecord {
  const data = asRecord(payload);
  return {
    tools: asRows(data.tools).map((tool) => ({
      name: str(tool.name),
      label: str(tool.label) || str(tool.name),
      source: str(tool.source),
      enabled: bool(tool.enabled),
      available: bool(tool.available),
    })),
  };
}

function projectCron(payload: unknown): JsonRecord {
  return {
    jobs: asRows(payload).map((job) => ({
      cron_id: str(job.cron_id) || str(job.id),
      name: str(job.name),
      prompt: str(job.prompt).slice(0, 200),
      trigger: str(job.trigger),
      enabled: job.enabled !== false,
      next_run_at: typeof job.next_run_at === "number" ? job.next_run_at : null,
      last_status: str(job.last_status),
    })),
  };
}

function projectKnowledgeBases(payload: unknown): JsonRecord {
  return {
    bases: asRows(payload).map((base) => ({
      knowledge_base_id: str(base.knowledge_base_id),
      name: str(base.name),
      description: str(base.description).slice(0, 300),
      doc_count: num(base.doc_count),
      max_documents: num(base.max_documents),
      icon_name: str(base.icon_name),
    })),
  };
}

export const SYSTEM_QUERY_HANDLERS: Record<
  string,
  (ctx: SystemQueryContext, params: SystemQueryParams) => Promise<unknown>
> = {
  "usage.summary": async (ctx, params) => {
    const window = safeWindow(params.window);
    const granularity = safeGranularity(params.granularity);
    const payload = await request<unknown>(
      `/usage/summary?window=${encodeURIComponent(
        window,
      )}&granularity=${encodeURIComponent(
        granularity,
      )}&agent_id=${encodeURIComponent(ctx.agentId)}`,
    );
    return projectUsageSummary(payload);
  },
  "agent.metrics": async (ctx, params) => {
    const window = safeWindow(params.window);
    return projectAgentMetrics(
      await request<unknown>(
        `/agents/${encodeURIComponent(
          ctx.agentId,
        )}/metrics?window=${encodeURIComponent(window)}`,
      ),
    );
  },
  "agent.inbox": async (ctx) =>
    projectAgentInbox(
      await request<unknown>(
        `/agents/${encodeURIComponent(ctx.agentId)}/inbox`,
      ),
    ),
  "agent.status": async (ctx) =>
    projectAgentStatus(
      await request<unknown>(
        `/agents/${encodeURIComponent(ctx.agentId)}/status`,
      ),
    ),
  "channels.list": async (ctx) =>
    projectChannels(
      await request<unknown>(
        `/agents/${encodeURIComponent(ctx.agentId)}/channels`,
      ),
    ),
  "skills.list": async (ctx) =>
    projectSkills(
      await request<unknown>(
        `/agents/${encodeURIComponent(ctx.agentId)}/skills`,
      ),
    ),
  "connectors.list": async () =>
    projectConnectors(await request<unknown>("/connector-instances")),
  "tools.summary": async (ctx) =>
    projectTools(
      await request<unknown>(
        `/agents/${encodeURIComponent(ctx.agentId)}/tool-settings`,
      ),
    ),
  "cron.list": async (ctx) =>
    projectCron(
      await request<unknown>(`/agents/${encodeURIComponent(ctx.agentId)}/cron`),
    ),
  "knowledge.bases": async () =>
    projectKnowledgeBases(await request<unknown>("/knowledge-bases")),
};

export async function runSystemQuery(
  key: string,
  params: SystemQueryParams,
  ctx: SystemQueryContext,
): Promise<unknown> {
  const handler = SYSTEM_QUERY_HANDLERS[key];
  if (!handler) throw new Error(`unknown system query: ${key}`);
  return handler(ctx, params);
}

export {
  projectAgentInbox,
  projectAgentMetrics,
  projectAgentStatus,
  projectChannels,
  projectConnectors,
  projectCron,
  projectKnowledgeBases,
  projectSkills,
  projectTools,
  projectUsageSummary,
};
