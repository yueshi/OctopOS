import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SystemAppFrame, { isSystemConsolePath } from "./SystemAppFrame";

describe("SystemAppFrame", () => {
  it("allows only known console deep links", () => {
    expect(isSystemConsolePath("/connectors")).toBe(true);
    expect(isSystemConsolePath("/personalization/channels?agent=agent_1")).toBe(
      true,
    );
    expect(isSystemConsolePath("/tasks")).toBe(true);
    expect(isSystemConsolePath("//evil.example")).toBe(false);
    expect(isSystemConsolePath("/admin/security")).toBe(false);
    expect(isSystemConsolePath("https://evil.example")).toBe(false);
  });

  it("renders the bundled page with the restricted SDK", async () => {
    render(<SystemAppFrame appKey="usage" agentId="agent_1" />);

    const frame = await screen.findByTestId("system-app-frame");
    expect(frame).toHaveAttribute("data-system-key", "usage");
    expect(frame).toHaveAttribute("data-agent-id", "agent_1");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");

    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain("octopSystem");
    expect(srcdoc).toContain("__OCTOP_LOCALE__");
    expect(srcdoc).not.toContain("octopApp");
  });
});
