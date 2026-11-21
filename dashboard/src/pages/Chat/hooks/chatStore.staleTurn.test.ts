import { afterEach, describe, expect, it } from "vitest";
import {
  attachThread,
  getSnapshot,
  ingestHarnessChunk,
  onStreamEvent,
  removeSession,
  sendTurn,
  type StreamEvent,
} from "./chatStore";

const SESSION = "test-stale-turn-stream";

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

describe("stale turn frames", () => {
  afterEach(() => {
    removeSession(SESSION);
  });

  it("ignores the previous turn's terminal frame before the new turn starts", async () => {
    const fake = installFakeWebSocket();
    try {
      const first = sendTurn(SESSION, "first", "agent-1", "", undefined);
      await Promise.resolve();
      const ws1 = fake.instances[0];
      expect(ws1).toBeDefined();
      ws1.onmessage?.(frame({ type: "turn.started", turn_id: "turn-a" }));
      ws1.onmessage?.(
        frame({ type: "token", content: "old answer", turn_id: "turn-a" }),
      );
      ws1.onmessage?.(frame({ type: "turn.ended", turn_id: "turn-a" }));
      await first;
      expect(getSnapshot(SESSION).isStreaming).toBe(false);

      const second = sendTurn(SESSION, "second", "agent-1", "", undefined);
      await Promise.resolve();
      const ws2 = fake.instances[1];
      expect(ws2).toBeDefined();
      // The old turn is still draining into the new socket.
      ws2.onmessage?.(
        frame({ type: "token", content: " leaked", turn_id: "turn-a" }),
      );
      ws2.onmessage?.(frame({ type: "turn.ended", turn_id: "turn-a" }));

      const during = getSnapshot(SESSION);
      expect(during.isStreaming).toBe(true);
      expect(ws2.readyState).toBe(1);
      expect(
        during.messages.some((message) => message.content.includes("leaked")),
      ).toBe(false);

      ws2.onmessage?.(frame({ type: "turn.started", turn_id: "turn-b" }));
      ws2.onmessage?.(
        frame({ type: "token", content: "new answer", turn_id: "turn-b" }),
      );
      // Even after the new turn started, late frames from the old one are dropped.
      ws2.onmessage?.(
        frame({ type: "token", content: " leaked again", turn_id: "turn-a" }),
      );
      ws2.onmessage?.(frame({ type: "turn.ended", turn_id: "turn-b" }));
      await second;

      const done = getSnapshot(SESSION);
      expect(done.isStreaming).toBe(false);
      expect(
        done.messages.some((message) => message.content.includes("new answer")),
      ).toBe(true);
      expect(
        done.messages.some((message) => message.content.includes("leaked")),
      ).toBe(false);
    } finally {
      fake.restore();
    }
  });

  it("caps an oversized live reasoning block", () => {
    ingestHarnessChunk(SESSION, {
      type: "reasoning",
      content: "r".repeat(40_000),
    });
    const message = getSnapshot(SESSION).messages[0];
    const thinking = message.contentBlocks?.find(
      (block) => block.type === "thinking",
    );
    expect(thinking?.content.length).toBeLessThanOrEqual(32_100);
    expect(thinking?.content).toContain("truncated");
  });

  it("marks a mid-turn attach incomplete so the UI can refetch history", async () => {
    const fake = installFakeWebSocket();
    const events: StreamEvent[] = [];
    const unsubscribe = onStreamEvent((event) => events.push(event));
    try {
      const attached = attachThread(SESSION, "agent-1", SESSION);
      await Promise.resolve();
      const ws = fake.instances[0];
      expect(ws).toBeDefined();
      ws.onmessage?.(frame({ type: "turn_status", active: true }));
      ws.onmessage?.(
        frame({ type: "token", content: "tail only", turn_id: "turn-a" }),
      );
      ws.onmessage?.(frame({ type: "turn.ended", turn_id: "turn-a" }));
      await attached;

      const end = events.find((event) => event.kind === "streamEnd");
      expect(end?.incomplete).toBe(true);
      expect(
        getSnapshot(SESSION).messages.some((m) =>
          m.content.includes("tail only"),
        ),
      ).toBe(true);
    } finally {
      unsubscribe();
      fake.restore();
    }
  });
});
