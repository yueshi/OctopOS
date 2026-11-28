import { afterEach, describe, expect, it } from "vitest";
import { getSnapshot, removeSession, sendTurn } from "./chatStore";

const SESSION = "test-turn-snapshot";

type FakeInstance = {
  readyState: number;
  sent: string[];
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send: (data: string) => void;
  close: () => void;
};

function installFakeWebSocket(): {
  instances: FakeInstance[];
  restore: () => void;
} {
  const instances: FakeInstance[] = [];
  class FakeWebSocket {
    static OPEN = 1;
    static CONNECTING = 0;
    static CLOSING = 2;
    static CLOSED = 3;
    readyState = 0;
    sent: string[] = [];
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;

    constructor() {
      instances.push(this as unknown as FakeInstance);
      queueMicrotask(() => {
        this.readyState = 1;
        this.onopen?.();
      });
    }

    send(data: string) {
      this.sent.push(data);
    }

    close() {
      this.readyState = 3;
      this.onclose?.();
    }
  }
  const RealWS = globalThis.WebSocket;
  globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
  return {
    instances,
    restore: () => {
      globalThis.WebSocket = RealWS;
    },
  };
}

function frame(payload: Record<string, unknown>): { data: string } {
  return { data: JSON.stringify({ thread_id: SESSION, ...payload }) };
}

function thinkingText(
  messages: ReturnType<typeof getSnapshot>["messages"],
): string {
  return messages
    .flatMap((message) => message.contentBlocks || [])
    .filter((block) => block.type === "thinking")
    .map((block) => block.content)
    .join("");
}

describe("turn snapshot replay", () => {
  afterEach(() => {
    removeSession(SESSION);
  });

  it("seeds a reloaded page with the text and thinking already streamed", async () => {
    const fake = installFakeWebSocket();
    try {
      const turn = sendTurn(SESSION, "hi", "agent-1", "", undefined);
      await Promise.resolve();
      const ws = fake.instances[0];
      ws.onmessage?.(frame({ type: "turn_status", active: true }));
      ws.onmessage?.(
        frame({
          type: "turn.snapshot",
          turn_id: "turn-1",
          reasoning: "deep thinking ",
          text: "partial answer",
          elapsed_s: 42,
        }),
      );

      const seeded = getSnapshot(SESSION);
      expect(seeded.isStreaming).toBe(true);
      expect(
        seeded.messages.some((message) =>
          message.content.includes("partial answer"),
        ),
      ).toBe(true);
      expect(thinkingText(seeded.messages)).toContain("deep thinking");

      ws.onmessage?.(
        frame({ type: "token", content: " continues", turn_id: "turn-1" }),
      );
      const continued = getSnapshot(SESSION);
      const answer = continued.messages.find((message) =>
        message.content.includes("partial answer"),
      );
      expect(answer?.content).toContain("partial answer continues");
      expect(answer?.content).not.toContain("partial answerpartial answer");

      ws.onmessage?.(frame({ type: "turn.ended", turn_id: "turn-1" }));
      await turn;
    } finally {
      fake.restore();
    }
  });

  it("ignores a snapshot when the socket already has live content", async () => {
    const fake = installFakeWebSocket();
    try {
      const turn = sendTurn(SESSION, "hi", "agent-1", "", undefined);
      await Promise.resolve();
      const ws = fake.instances[0];
      ws.onmessage?.(frame({ type: "turn.started", turn_id: "turn-1" }));
      ws.onmessage?.(
        frame({ type: "token", content: "live", turn_id: "turn-1" }),
      );
      ws.onmessage?.(
        frame({
          type: "turn.snapshot",
          turn_id: "turn-1",
          text: "stale",
          reasoning: "old",
          elapsed_s: 10,
        }),
      );
      const snap = getSnapshot(SESSION);
      expect(
        snap.messages.some((message) => message.content.includes("live")),
      ).toBe(true);
      expect(
        snap.messages.some((message) => message.content.includes("stale")),
      ).toBe(false);
      ws.onmessage?.(frame({ type: "turn.ended", turn_id: "turn-1" }));
      await turn;
    } finally {
      fake.restore();
    }
  });
});
