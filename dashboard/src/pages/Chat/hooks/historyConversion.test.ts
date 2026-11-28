import { describe, expect, it } from "vitest";
import { convertHistoryMessages } from "./useChat";

describe("history conversion", () => {
  it("splits an assistant row with a tool call so narration is not dropped", () => {
    const messages = convertHistoryMessages([
      {
        role: "assistant",
        id: "a1",
        content: [
          { type: "thinking", thinking: "think" },
          { type: "text", text: "before" },
          {
            type: "tool_use",
            name: "read_file",
            id: "c1",
            input: { path: "x" },
          },
        ],
      },
      {
        role: "tool",
        id: "t1",
        content: [
          { type: "tool_result", id: "c1", name: "read_file", output: "done" },
        ],
      },
      { role: "assistant", id: "a2", content: [{ type: "text", text: "after" }] },
    ]);

    expect(messages.map((message) => message.role)).toEqual([
      "assistant",
      "assistant",
      "assistant",
    ]);
    expect(messages[0]?.contentBlocks?.map((block) => block.type)).toEqual([
      "thinking",
      "text",
    ]);
    expect(messages[0]?.content).toContain("before");
    expect(messages[0]?.toolData).toBeUndefined();
    expect(messages[1]?.toolData?.name).toBe("read_file");
    expect(messages[1]?.toolData?.output).toBe("done");
    expect(messages[2]?.content).toBe("after");
  });
});
