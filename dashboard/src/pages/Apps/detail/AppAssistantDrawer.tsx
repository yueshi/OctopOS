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
} from "../../../api/modules/apps";
import Markdown from "../../../components/Markdown";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "../../../utils/antdMessage";

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
  const [assistantTool, setAssistantTool] = useState("");
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

  const sendAssistant = async () => {
    const question = assistantInput.trim();
    if (!question || assistantBusy) return;
    setAssistantInput("");
    setAssistantError("");
    setAssistantBusy(true);
    setAssistantMsgs((prev) => [
      ...prev,
      { role: "user", content: question, created_at: 0 },
      { role: "assistant", content: "", created_at: 0 },
    ]);
    try {
      await askAppAssistant(appId, question, (event) => {
        if (event.type === "token") {
          setAssistantMsgs((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                content: last.content + event.text,
              };
            }
            return next;
          });
        } else if (event.type === "tool") {
          setAssistantTool(event.name);
        } else if (event.type === "chart") {
          setAssistantMsgs((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                charts: [...(last.charts ?? []), event.chart],
              };
            }
            return next;
          });
        } else if (event.type === "done") {
          const citations = event.citations ?? [];
          const charts = event.charts;
          setAssistantMsgs((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                citations,
                // `done` carries the authoritative list; chart events are for
                // rendering while the answer is still streaming.
                ...(charts ? { charts } : {}),
              };
            }
            return next;
          });
        } else if (event.type === "error") {
          setAssistantError(event.message);
        }
      });
    } catch (err) {
      setAssistantError(
        apiErrorMessage(err, t("apps.assistantRequestFailed"), t),
      );
    } finally {
      setAssistantTool("");
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
  }, [assistantMsgs, assistantTool, open]);

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
              disabled={assistantBusy}
              onPressEnter={() => void sendAssistant()}
            />
            <Button
              type="primary"
              loading={assistantBusy}
              disabled={!assistantInput.trim()}
              onClick={() => void sendAssistant()}
            >
              {t("apps.assistantSend")}
            </Button>
          </Space.Compact>
          <Text
            type="secondary"
            style={{ fontSize: 12, display: "block", marginTop: 6 }}
          >
            {t("apps.assistantHint")}
          </Text>
        </div>
      }
    >
      {/* Resize grip: absolute against the drawer content-wrapper (the
          nearest positioned ancestor), so it spans the full panel height
          and escapes the scrolling body's clipping. */}
      <div
        onPointerDown={startAssistantResize}
        onPointerMove={moveAssistantResize}
        onPointerUp={endAssistantResize}
        onPointerCancel={endAssistantResize}
        onMouseEnter={() => setAssistantResizeHover(true)}
        onMouseLeave={() => setAssistantResizeHover(false)}
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 8,
          cursor: "col-resize",
          zIndex: 10,
          touchAction: "none",
          background:
            assistantResizeHover || assistantResizing
              ? "rgba(22, 119, 255, 0.18)"
              : "transparent",
        }}
      />
      <div
        ref={assistantListRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {assistantLoading ? (
          <div style={{ padding: 24, textAlign: "center" }}>
            <Spin />
          </div>
        ) : (
          <>
            {assistantMsgs.length === 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t("apps.assistantEmpty")}
              </Text>
            )}
            {assistantMsgs.map((item, index) => {
              const isUser = item.role === "user";
              const hasCharts = Boolean(item.charts?.length);
              const streaming =
                assistantBusy && index === assistantMsgs.length - 1;
              return (
                <div
                  key={index}
                  style={{
                    // Charts need a concrete parent width for
                    // ResponsiveContainer; give the bubble the full drawer
                    // width (capped on wide screens) instead of letting it
                    // shrink to the chart's min-content width.
                    alignSelf: hasCharts
                      ? "flex-start"
                      : isUser
                      ? "flex-end"
                      : "flex-start",
                    width: hasCharts ? "100%" : undefined,
                    maxWidth: hasCharts ? "min(100%, 720px)" : "85%",
                    minWidth: 0,
                    boxSizing: "border-box",
                    background: isUser
                      ? "rgba(22, 119, 255, 0.08)"
                      : "rgba(0, 0, 0, 0.03)",
                    padding: "8px 12px",
                    borderRadius: 10,
                    whiteSpace: isUser ? "pre-wrap" : "normal",
                    wordBreak: "break-word",
                    fontSize: 13,
                  }}
                >
                  {item.content ? (
                    isUser ? (
                      item.content
                    ) : (
                      <Markdown
                        content={item.content}
                        isStreaming={streaming}
                      />
                    )
                  ) : streaming ? (
                    <Spin size="small" />
                  ) : hasCharts ? null : (
                    "…"
                  )}
                  {!isUser && hasCharts ? (
                    <div
                      style={{
                        marginTop: 8,
                        display: "grid",
                        gap: 12,
                        minWidth: 0,
                      }}
                    >
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
                  {!isUser && item.citations?.length ? (
                    <div
                      style={{
                        marginTop: 6,
                        display: "flex",
                        gap: 8,
                        flexWrap: "wrap",
                        fontSize: 12,
                      }}
                    >
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
              );
            })}
            {assistantTool && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                <Spin size="small" style={{ marginRight: 6 }} />
                {t("apps.assistantToolRunning", { name: assistantTool })}
              </Text>
            )}
            {assistantError && (
              <Text type="danger" style={{ fontSize: 12 }}>
                {assistantError}
              </Text>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}
