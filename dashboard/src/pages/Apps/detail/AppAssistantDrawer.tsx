import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Button, Drawer, Input, Space, Spin, Tooltip, Typography } from "antd";
import {
  AlignCenterHorizontal,
  ChevronsLeft,
  ChevronsRight,
  Sparkles,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  appsApi,
  askAppAssistant,
  type AppAssistantChart,
  type AppAssistantMessage,
  type AppAssistantProcessStep,
} from "../../../api/modules/apps";
import Markdown from "../../../components/Markdown";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";
import AppAssistantProcess from "./AppAssistantProcess";
import styles from "./AppAssistantDrawer.module.less";

const { Text } = Typography;

const AssistantChart = lazy(() => import("../AssistantChart"));

/** Drawer width presets (px) shared by the drag handle and the min/mid/max
 *  quick buttons; min and max follow the viewport. */
const assistantMinWidth = () => Math.floor(window.innerWidth * 0.35);
const assistantMaxWidth = () =>
  Math.max(assistantMinWidth(), Math.floor(window.innerWidth * 0.9));

interface Props {
  appId: string;
  open: boolean;
  onClose: () => void;
  onOpenCitation: (table: string) => void;
}

export default function AppAssistantDrawer({
  appId,
  open,
  onClose,
  onOpenCitation,
}: Props) {
  const { t } = useTranslation();
  const [assistantMsgs, setAssistantMsgs] = useState<AppAssistantMessage[]>([]);
  const [assistantLoading, setAssistantLoading] = useState(false);
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantError, setAssistantError] = useState("");
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantWidth, setAssistantWidth] = useState(assistantMinWidth);
  const [assistantResizing, setAssistantResizing] = useState(false);
  const [assistantResizeHover, setAssistantResizeHover] = useState(false);
  const assistantListRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setAssistantLoading(true);
    appsApi
      .assistantHistory(appId)
      .then((data) => {
        if (!cancelled) setAssistantMsgs(data.messages);
      })
      .catch((err) => {
        if (!cancelled) {
          message.error(apiErrorMessage(err, t("apps.assistantLoadFailed"), t));
        }
      })
      .finally(() => {
        if (!cancelled) setAssistantLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, appId, t]);

  /** Patch the streaming assistant turn (the last message). */
  const updateStreamingAssistant = (
    updater: (message: AppAssistantMessage) => AppAssistantMessage,
  ) => {
    setAssistantMsgs((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last?.role !== "assistant") return prev;
      next[next.length - 1] = updater(last);
      return next;
    });
  };

  const appendProcessStep = (step: AppAssistantProcessStep) => {
    updateStreamingAssistant((last) => ({
      ...last,
      process: [...(last.process ?? []), step],
    }));
  };

  const sendAssistant = async () => {
    const question = assistantInput.trim();
    // History may still be loading; let it land before appending a new turn,
    // otherwise the fetch would overwrite the just-sent messages.
    if (!question || assistantBusy || assistantLoading) return;
    setAssistantInput("");
    setAssistantError("");
    setAssistantBusy(true);
    setAssistantMsgs((prev) => [
      ...prev,
      { role: "user", content: question, created_at: 0 },
      { role: "assistant", content: "", created_at: 0, process: [] },
    ]);
    try {
      await askAppAssistant(appId, question, (event) => {
        if (event.type === "token") {
          updateStreamingAssistant((last) => ({
            ...last,
            content: last.content + event.text,
          }));
        } else if (event.type === "thinking") {
          appendProcessStep({ kind: "thinking", text: event.text });
        } else if (event.type === "tool_start") {
          appendProcessStep({
            kind: "tool",
            call_id: event.call_id,
            name: event.name,
            arguments: event.arguments,
            status: "running",
          });
        } else if (event.type === "tool_end") {
          updateStreamingAssistant((last) => ({
            ...last,
            process: (last.process ?? []).map((step) =>
              step.kind === "tool" && step.call_id === event.call_id
                ? { ...step, result: event.result, status: event.status }
                : step,
            ),
          }));
        } else if (event.type === "chart") {
          updateStreamingAssistant((last) => ({
            ...last,
            charts: [...(last.charts ?? []), event.chart],
          }));
        } else if (event.type === "done") {
          const citations = event.citations ?? [];
          const charts = event.charts;
          updateStreamingAssistant((last) => ({
            ...last,
            citations,
            // `done` carries the authoritative list; chart events are for
            // rendering while the answer is still streaming.
            ...(charts ? { charts } : {}),
          }));
        } else if (event.type === "error") {
          setAssistantError(event.message);
        }
      });
    } catch (err) {
      setAssistantError(
        apiErrorMessage(err, t("apps.assistantRequestFailed"), t),
      );
    } finally {
      setAssistantBusy(false);
    }
  };

  // Drag the handle on the drawer's left edge to resize. Pointer capture
  // keeps the drag alive outside the handle; the drawer wrapper's own
  // width transition is disabled while dragging so it follows the cursor.
  const startAssistantResize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    setAssistantResizing(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const moveAssistantResize = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!assistantResizing) return;
    setAssistantWidth(
      Math.min(
        Math.max(window.innerWidth - e.clientX, assistantMinWidth()),
        assistantMaxWidth(),
      ),
    );
  };
  const endAssistantResize = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!assistantResizing) return;
    setAssistantResizing(false);
    e.currentTarget.releasePointerCapture(e.pointerId);
  };
  const setAssistantPreset = (width: number) =>
    setAssistantWidth(
      Math.min(Math.max(width, assistantMinWidth()), assistantMaxWidth()),
    );

  useEffect(() => {
    const el = assistantListRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [assistantMsgs, open]);

  return (
    <Drawer
      title={
        <Space size={8}>
          <Sparkles size={16} />
          {t("apps.assistantTitle")}
        </Space>
      }
      open={open}
      onClose={onClose}
      width={assistantWidth}
      extra={
        // The drawer hugs the right edge: dragging its left border outward
        // (leftwards) widens it, inwards shrinks it — the chevrons mirror
        // exactly that, so left = widen, right = narrow.
        <Space size={0}>
          <Tooltip title={t("apps.assistantWidthMax")}>
            <Button
              type="text"
              size="small"
              aria-label={t("apps.assistantWidthMax")}
              icon={<ChevronsLeft size={14} />}
              onClick={() => setAssistantPreset(assistantMaxWidth())}
            />
          </Tooltip>
          <Tooltip title={t("apps.assistantWidthMid")}>
            <Button
              type="text"
              size="small"
              aria-label={t("apps.assistantWidthMid")}
              icon={<AlignCenterHorizontal size={14} />}
              onClick={() =>
                setAssistantPreset(Math.floor(window.innerWidth * 0.5))
              }
            />
          </Tooltip>
          <Tooltip title={t("apps.assistantWidthMin")}>
            <Button
              type="text"
              size="small"
              aria-label={t("apps.assistantWidthMin")}
              icon={<ChevronsRight size={14} />}
              onClick={() => setAssistantPreset(assistantMinWidth())}
            />
          </Tooltip>
        </Space>
      }
      styles={{
        body: { display: "flex", flexDirection: "column" },
        // antd animates wrapper width changes; freeze it mid-drag.
        wrapper: { transition: assistantResizing ? "none" : undefined },
      }}
      footer={
        <div>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              value={assistantInput}
              onChange={(event) => setAssistantInput(event.target.value)}
              placeholder={t("apps.assistantPlaceholder")}
              maxLength={4000}
              disabled={assistantBusy || assistantLoading}
              onPressEnter={() => void sendAssistant()}
            />
            <Button
              type="primary"
              loading={assistantBusy}
              disabled={assistantLoading || !assistantInput.trim()}
              onClick={() => void sendAssistant()}
            >
              {t("apps.assistantSend")}
            </Button>
          </Space.Compact>
          <Text type="secondary" className={styles.hint}>
            {t("apps.assistantHint")}
          </Text>
        </div>
      }
    >
      {/* Resize grip: absolute against the drawer content-wrapper (the
          nearest positioned ancestor), so it spans the full panel height
          and escapes the scrolling body's clipping. */}
      <div
        className={styles.resizeHandle}
        data-active={
          assistantResizeHover || assistantResizing ? "true" : "false"
        }
        onPointerDown={startAssistantResize}
        onPointerMove={moveAssistantResize}
        onPointerUp={endAssistantResize}
        onPointerCancel={endAssistantResize}
        onMouseEnter={() => setAssistantResizeHover(true)}
        onMouseLeave={() => setAssistantResizeHover(false)}
      />
      <div ref={assistantListRef} className={styles.messageList}>
        {assistantLoading ? (
          <div className={styles.loading}>
            <Spin />
          </div>
        ) : (
          <>
            {assistantMsgs.length === 0 && (
              <Text type="secondary" className={styles.empty}>
                {t("apps.assistantEmpty")}
              </Text>
            )}
            {assistantMsgs.map((item, index) => {
              const isUser = item.role === "user";
              const hasCharts = Boolean(item.charts?.length);
              const streaming =
                assistantBusy && index === assistantMsgs.length - 1;
              if (isUser) {
                return (
                  <div key={index} className={styles.userRow}>
                    <div className={`${styles.bubble} ${styles.userBubble}`}>
                      {item.content}
                    </div>
                  </div>
                );
              }
              return (
                <div key={index} className={styles.assistantRow}>
                  <span className={styles.avatar} aria-hidden>
                    <Sparkles size={14} />
                  </span>
                  <div className={styles.messageColumn}>
                    <AppAssistantProcess
                      steps={item.process ?? []}
                      streaming={streaming}
                    />
                    <div
                      className={`${styles.bubble} ${styles.assistantBubble}`}
                    >
                      {item.content ? (
                        <Markdown
                          content={item.content}
                          isStreaming={streaming}
                        />
                      ) : streaming ? (
                        <Spin size="small" />
                      ) : hasCharts ? null : (
                        "…"
                      )}
                    </div>
                    {hasCharts ? (
                      <div className={styles.charts}>
                        {item.charts?.map(
                          (chart: AppAssistantChart, chartIndex) => (
                            <Suspense
                              key={`${chart.title}-${chartIndex}`}
                              fallback={<Spin size="small" />}
                            >
                              <AssistantChart chart={chart} />
                            </Suspense>
                          ),
                        )}
                      </div>
                    ) : null}
                    {item.citations?.length ? (
                      <div className={styles.citations}>
                        <Text type="secondary">
                          {t("apps.assistantCitations")}:
                        </Text>
                        {item.citations.slice(0, 5).map((citation) => (
                          <a
                            key={`${citation.table}-${citation.row_id}`}
                            onClick={() => onOpenCitation(citation.table)}
                          >
                            {citation.row_id}
                          </a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
            {assistantError && (
              <Text type="danger" className={styles.error}>
                {assistantError}
              </Text>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}
