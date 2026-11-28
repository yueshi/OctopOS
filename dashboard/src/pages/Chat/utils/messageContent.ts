import type { ChatMessage, DecisionNotice } from "../hooks/useChat";
import { isBrowserToolName, isWriteToolName } from "../constants";

const THINKING_TAG_RE = /<(?:think|thinking)>[\s\S]*?<\/(?:think|thinking)>/gi;
const THINKING_OPEN_RE = /<(?:think|thinking)>/i;
const THINKING_CLOSE_RE = /<\/(?:think|thinking)>/i;

/** Strip `<think>` / `<thinking>` blocks from visible assistant text. */
export function stripThinkTags(raw: string): string {
  let result = raw.replace(THINKING_TAG_RE, "");
  // Orphan closing tag: drop the hidden prefix before it.
  while (true) {
    const close = result.search(THINKING_CLOSE_RE);
    if (close < 0) break;
    const open = result.search(THINKING_OPEN_RE);
    if (open >= 0 && open < close) break;
    result = result.slice(close).replace(THINKING_CLOSE_RE, "");
  }
  const unclosed = result.search(THINKING_OPEN_RE);
  if (unclosed >= 0) {
    result = result.slice(0, unclosed);
  }
  return result.trim();
}

export function deriveMessageContent(message: ChatMessage): {
  thinkingParts: string[];
  textContent: string;
} {
  if (message.contentBlocks && message.contentBlocks.length > 0) {
    const thinkingParts: string[] = [];
    let text = "";
    for (const block of message.contentBlocks) {
      if (block.type === "thinking" && block.content.trim()) {
        thinkingParts.push(block.content);
      } else if (block.type === "text") {
        text += block.content;
      }
    }
    if (text) {
      text = stripThinkTags(text);
    } else if (message.content) {
      text = stripThinkTags(message.content);
    }
    return { thinkingParts, textContent: text };
  }

  if (message.type === "reasoning" && message.content) {
    return { thinkingParts: [message.content], textContent: "" };
  }

  return {
    thinkingParts: [],
    textContent: message.content ? stripThinkTags(message.content) : "",
  };
}

export interface ThinkingProcessItem {
  messageId: string;
  content: string;
  isStreaming?: boolean;
}

export type ProcessStep =
  | { kind: "thinking"; item: ThinkingProcessItem }
  | { kind: "text"; item: ThinkingProcessItem }
  | { kind: "tool"; message: ChatMessage }
  | { kind: "decision"; notice: DecisionNotice };

export interface AssistantTurnSplit {
  tools: ChatMessage[];
  thinkings: ThinkingProcessItem[];
  processSteps: ProcessStep[];
  answerMessage: ChatMessage | null;
  /** Stream failures stay out of the answer so the error box is only the error. */
  errorMessages?: ChatMessage[];
}

function joinAnswerParts(parts: string[]): string {
  let out = "";
  for (const part of parts) {
    if (!part) continue;
    if (!out) {
      out = part;
      continue;
    }
    if (part === out || out.endsWith(part)) continue;
    if (part.startsWith(out) || part.endsWith(out)) {
      out = part.length > out.length ? part : out;
      continue;
    }
    const needSpace =
      !/\s$/u.test(out) &&
      !/^\s/u.test(part) &&
      !/^[，。！？、,.!?;:]/u.test(part);
    out += needSpace ? ` ${part}` : part;
  }
  return out;
}

/** True when a completed tool sits between two visible text bubbles (1:1 ReAct). */
function hasCompletedToolBetweenTexts(messages: ChatMessage[]): boolean {
  let sawText = false;
  let sawToolAfterText = false;
  for (const msg of messages) {
    const { textContent } = deriveMessageContent(msg);
    if (msg.toolData?.output && sawText) {
      sawToolAfterText = true;
    }
    if (!msg.toolData && textContent.trim() && sawToolAfterText) {
      return true;
    }
    if (!msg.toolData && textContent.trim()) {
      sawText = true;
    }
  }
  return false;
}

/** Ordered thinking/text segments inside one assistant message. */
function orderedContentBlocks(
  message: ChatMessage,
): Array<{ type: "thinking" | "text"; content: string }> {
  const out: Array<{ type: "thinking" | "text"; content: string }> = [];
  if (message.contentBlocks && message.contentBlocks.length > 0) {
    for (const block of message.contentBlocks) {
      if (block.type === "thinking" && block.content.trim()) {
        out.push({ type: "thinking", content: block.content });
      } else if (block.type === "text") {
        const text = stripThinkTags(block.content);
        if (text.trim()) out.push({ type: "text", content: text });
      }
    }
    if (out.length > 0) return out;
  }
  if (message.type === "reasoning" && message.content) {
    return [{ type: "thinking", content: message.content }];
  }
  const text = message.content ? stripThinkTags(message.content) : "";
  return text.trim() ? [{ type: "text", content: text }] : [];
}

export function splitAssistantTurn(
  messages: ChatMessage[],
  opts?: { joinAnswerFragments?: boolean },
): AssistantTurnSplit {
  const tools: ChatMessage[] = [];
  const thinkings: ThinkingProcessItem[] = [];
  const timeline: ProcessStep[] = [];
  const textEntries: Array<{
    item: ThinkingProcessItem;
    message: ChatMessage;
    timelineIndex: number;
  }> = [];
  const errorMessages: ChatMessage[] = [];
  const decision = messages.find((item) => item.decision)?.decision;
  if (decision) {
    timeline.push({ kind: "decision", notice: decision });
  }

  for (const msg of messages) {
    if (msg.toolData) {
      tools.push(msg);
      timeline.push({ kind: "tool", message: msg });
      continue;
    }
    if (msg.status === "error") {
      const { textContent } = deriveMessageContent(msg);
      const errorText =
        textContent.trim() || msg.errorInfo?.message?.trim() || "";
      errorMessages.push({
        ...msg,
        content: errorText,
        contentBlocks: undefined,
      });
      continue;
    }
    const blocks = orderedContentBlocks(msg);
    const hasText = blocks.some((block) => block.type === "text");
    for (const block of blocks) {
      const item: ThinkingProcessItem = {
        messageId: msg.id,
        content: block.content,
        isStreaming: msg.status === "streaming",
      };
      if (block.type === "thinking") {
        item.isStreaming = msg.status === "streaming" && !hasText;
        thinkings.push(item);
        timeline.push({ kind: "thinking", item });
      } else {
        const timelineIndex = timeline.length;
        textEntries.push({ item, message: msg, timelineIndex });
        timeline.push({ kind: "text", item });
      }
    }
  }

  // 1:1 ReAct (text → tool → conclusion) keeps the last bubble as the answer
  // once the turn settles. While generating (or for stamped team speakers),
  // join fragments so the answer never shrinks mid-flight after a tool round.
  const textParts = textEntries.map((entry) => entry.item.content);
  const teamStamped = messages.some((item) =>
    Boolean((item.speakerAgentId || "").trim()),
  );
  const joinFragments =
    textParts.length > 1 &&
    (Boolean(opts?.joinAnswerFragments) ||
      teamStamped ||
      messages.some((item) => item.teamWrapup) ||
      !hasCompletedToolBetweenTexts(messages));

  // Text after the last *completed* tool is the answer group. If the turn
  // stopped on a tool/approval with no trailing text, the last text before it
  // is still the last visible output and must remain the answer. A pending
  // approval / in-flight tool (no output yet) does not end a narration round.
  const lastToolTimelineIndex = timeline.reduce(
    (last, step, index) =>
      step.kind === "tool" && step.message.toolData?.output !== undefined
        ? index
        : last,
    -1,
  );
  const trailingTextEntries = textEntries.filter(
    (entry) => entry.timelineIndex > lastToolTimelineIndex,
  );
  const answerEntries = joinFragments
    ? textEntries
    : trailingTextEntries.length > 0
    ? trailingTextEntries
    : textEntries;
  const answerEntry = answerEntries[answerEntries.length - 1];
  const answerMessage =
    answerEntry && answerEntries.length > 0
      ? {
          ...answerEntry.message,
          content: joinFragments
            ? joinAnswerParts(textParts)
            : answerEntry.item.content,
          contentBlocks: undefined,
          status:
            answerEntry.message.status === "streaming"
              ? ("streaming" as const)
              : answerEntry.message.status,
        }
      : null;

  // Intermediate outputs (everything before the final answer) stay inside the
  // process fold, in the same order as thinking/tool steps. Joining them into
  // the answer would mix rounds; dropping them loses the real transcript.
  let processSteps = timeline;
  if (textEntries.length > 0) {
    if (joinFragments) {
      processSteps = timeline.filter((step) => step.kind !== "text");
    } else {
      const intermediate = new Set(
        textEntries
          .filter(
            (entry) =>
              answerEntry !== undefined &&
              entry.timelineIndex < answerEntry.timelineIndex,
          )
          .map((entry) => entry.item),
      );
      processSteps = timeline.filter(
        (step) => step.kind !== "text" || intermediate.has(step.item),
      );
    }
  }

  return { tools, thinkings, processSteps, answerMessage, errorMessages };
}

export function toAnswerOnlyMessage(message: ChatMessage): ChatMessage {
  const { textContent } = deriveMessageContent(message);
  const textBlocks =
    message.contentBlocks?.filter((block) => block.type === "text") ?? [];
  return {
    ...message,
    content: textContent,
    contentBlocks: textBlocks.length > 0 ? textBlocks : undefined,
    toolData: undefined,
  };
}

export function countProcessStats(split: AssistantTurnSplit): {
  toolCount: number;
  thinkingCount: number;
} {
  const tools = split?.tools ?? [];
  const thinkings = split?.thinkings ?? [];
  return {
    toolCount: tools.length,
    thinkingCount: thinkings.length,
  };
}

export function turnUsedBrowserTool(split: AssistantTurnSplit): boolean {
  return (split?.tools ?? []).some((msg) =>
    isBrowserToolName(msg.toolData?.name),
  );
}

/** True when this message is a workspace file write/edit tool call. */
function messageUsesFileTool(msg: ChatMessage): boolean {
  return isWriteToolName(msg.toolData?.name);
}

/** True when this assistant turn invoked a workspace file write/edit tool. */
export function turnUsedFileTool(split: AssistantTurnSplit): boolean {
  if ((split?.tools ?? []).some((msg) => messageUsesFileTool(msg))) {
    return true;
  }
  return (split?.answerMessage?.editedFiles?.length ?? 0) > 0;
}

/** Index of the most recent assistant turn that invoked a browser tool, or -1. */
export function findLastBrowserTurnGroupIndex(
  groups: ReadonlyArray<{ messages: ReadonlyArray<ChatMessage> }>,
): number {
  for (let i = groups.length - 1; i >= 0; i--) {
    const group = groups[i];
    if (!group.messages.some((m) => m.role === "assistant")) continue;
    if (turnUsedBrowserTool(splitAssistantTurn([...group.messages]))) return i;
  }
  return -1;
}
