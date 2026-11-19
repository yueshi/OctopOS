import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  Check,
  ChevronRight,
  ChevronUp,
  Database,
  ListTree,
  Loader2,
  Sparkles,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AppAssistantProcessStep } from "../../../api/modules/apps";
import Markdown from "../../../components/Markdown";
import styles from "./AppAssistantProcess.module.less";

interface ToolDisplay {
  labelKey: string;
  label: string;
  Icon: typeof Database;
}

const TOOL_DISPLAY: Record<string, ToolDisplay> = {
  app_tables: {
    labelKey: "apps.assistantTools.appTables",
    label: "List tables",
    Icon: ListTree,
  },
  app_query_rows: {
    labelKey: "apps.assistantTools.appQueryRows",
    label: "Query rows",
    Icon: Database,
  },
  app_chart: {
    labelKey: "apps.assistantTools.appChart",
    label: "Build chart",
    Icon: BarChart3,
  },
};

function ToolStep({ step }: { step: AppAssistantProcessStep }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const display = (step.name && TOOL_DISPLAY[step.name]) || {
    labelKey: "apps.assistantTools.generic",
    label: "Tool",
    Icon: Wrench,
  };
  const Icon = display.Icon;
  const status = step.status ?? "ok";
  const args = useMemo(
    () =>
      step.arguments && Object.keys(step.arguments).length > 0
        ? JSON.stringify(step.arguments, null, 2)
        : "",
    [step.arguments],
  );
  const statusLabel =
    status === "running"
      ? t("apps.assistantToolRunningShort", "Running")
      : status === "error"
      ? t("apps.assistantToolFailed", "Failed")
      : t("common.done", "Done");

  return (
    <div className={styles.tool}>
      <button
        type="button"
        className={styles.toolToggle}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={styles.toolIcon}>
          <Icon size={13} aria-hidden />
        </span>
        <span className={styles.toolName}>
          {t(display.labelKey, display.label)}
        </span>
        <span className={styles.toolStatus} data-status={status}>
          {status === "running" ? (
            <Loader2 size={12} className={styles.spin} aria-hidden />
          ) : status === "error" ? (
            <AlertCircle size={12} aria-hidden />
          ) : (
            <Check size={12} aria-hidden />
          )}
          {statusLabel}
        </span>
        <ChevronRight
          size={13}
          className={open ? styles.chevronOpen : styles.chevron}
          aria-hidden
        />
      </button>
      {open && (args || step.result) ? (
        <div className={styles.toolDetails}>
          {args ? (
            <div>
              <span className={styles.detailLabel}>
                {t("apps.assistantToolArguments", "Arguments")}
              </span>
              <pre>{args}</pre>
            </div>
          ) : null}
          {step.result ? (
            <div>
              <span className={styles.detailLabel}>
                {t("apps.assistantToolResult", "Result")}
              </span>
              <pre>{step.result}</pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

interface AppAssistantProcessProps {
  steps: readonly AppAssistantProcessStep[];
  streaming: boolean;
}

/**
 * Folded thinking + tool-call timeline for one assistant answer.
 *
 * Mirrors the Agent chat's process summary: collapsed for history, expanded
 * while the turn is streaming, with a live hint; expanding shows the ordered
 * thinking blocks and tool rows (arguments/result on demand).
 */
export default function AppAssistantProcess({
  steps,
  streaming,
}: AppAssistantProcessProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(streaming);
  const prevStreaming = useRef(streaming);

  useEffect(() => {
    if (prevStreaming.current && !streaming) setExpanded(false);
    prevStreaming.current = streaming;
  }, [streaming]);

  const { thinkingCount, toolCount, runningTool } = useMemo(() => {
    let thinking = 0;
    let tools = 0;
    let running: AppAssistantProcessStep | undefined;
    for (const step of steps) {
      if (step.kind === "thinking") {
        if (step.text) thinking += 1;
      } else if (step.kind === "tool") {
        tools += 1;
        if (step.status === "running") running = step;
      }
    }
    return { thinkingCount: thinking, toolCount: tools, runningTool: running };
  }, [steps]);

  if (thinkingCount === 0 && toolCount === 0) return null;

  const summary =
    toolCount > 0 && thinkingCount > 0
      ? t("chat.processSummary", {
          tools: toolCount,
          thinking: thinkingCount,
          defaultValue: "Called {{tools}} tools, {{thinking}} deep thoughts",
        })
      : toolCount > 0
      ? t("chat.processSummaryToolsOnly", {
          tools: toolCount,
          defaultValue: "Called {{tools}} tools",
        })
      : t("chat.processSummaryThinkingOnly", {
          thinking: thinkingCount,
          defaultValue: "{{thinking}} deep thoughts",
        });

  const liveHint = streaming
    ? runningTool
      ? t("chat.processRunningTools", "Calling tools")
      : t("chat.processOrganizing", "Organizing results")
    : null;

  return (
    <div className={styles.process}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={expanded}
        aria-busy={liveHint ? true : undefined}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className={styles.summaryText}>
          {liveHint ? (
            <span className={styles.live}>
              <Loader2 size={12} className={styles.spin} aria-hidden />
              {liveHint}
            </span>
          ) : (
            summary
          )}
        </span>
        <ChevronRight
          size={14}
          className={expanded ? styles.chevronOpen : styles.chevron}
          aria-hidden
        />
      </button>
      {expanded ? (
        <div className={styles.body}>
          {steps.map((step, index) =>
            step.kind === "thinking" ? (
              <div key={`thinking-${index}`} className={styles.thinking}>
                <div className={styles.stepHeader}>
                  <Sparkles size={12} aria-hidden />
                  {t("apps.assistantThinking", "Deep thinking")}
                </div>
                <div className={styles.thinkingBody}>
                  <Markdown content={step.text ?? ""} />
                </div>
              </div>
            ) : (
              <ToolStep key={step.call_id || `tool-${index}`} step={step} />
            ),
          )}
          <button
            type="button"
            className={styles.collapse}
            onClick={() => setExpanded(false)}
          >
            <ChevronUp size={13} aria-hidden />
            {t("chat.processCollapse", "Collapse")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
