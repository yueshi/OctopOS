import { describe, expect, it } from "vitest";

import {
  ALL_CAPABILITIES,
  CAP,
  SYSTEM_CAP,
  effectiveCapabilities,
  isMethodAllowed,
} from "./capabilities";

describe("MiniApp capabilities", () => {
  it("treats an older payload without a manifest as legacy all-on", () => {
    expect(effectiveCapabilities(undefined)).toContain(CAP.AI_GENERATE);
    expect(isMethodAllowed("generate", undefined)).toBe(true);
  });

  it("gates page methods by the declared manifest", () => {
    expect(isMethodAllowed("rows", [CAP.DATA_READ])).toBe(true);
    expect(isMethodAllowed("append", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("generate", [CAP.AI_GENERATE])).toBe(true);
    expect(isMethodAllowed("launchGame", [CAP.AI_GENERATE])).toBe(false);
    expect(isMethodAllowed("asset", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("composeMessage", [CAP.CHAT_COMPOSE])).toBe(true);
    expect(isMethodAllowed("composeMessage", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("service", [CAP.EXTERNAL_FETCH])).toBe(true);
    expect(isMethodAllowed("service", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("serviceDetailed", [CAP.EXTERNAL_FETCH])).toBe(true);
    expect(isMethodAllowed("serviceDetailed", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("serviceImage", [CAP.EXTERNAL_FETCH])).toBe(true);
    expect(isMethodAllowed("serviceImage", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("events", [CAP.EVENTS_READ])).toBe(true);
    expect(isMethodAllowed("ackEvents", [CAP.EVENTS_READ])).toBe(true);
    expect(isMethodAllowed("events", [CAP.DATA_READ])).toBe(false);
  });

  it("gates the row write methods behind data.write", () => {
    expect(isMethodAllowed("update", [CAP.DATA_WRITE])).toBe(true);
    expect(isMethodAllowed("updateMany", [CAP.DATA_WRITE])).toBe(true);
    expect(isMethodAllowed("remove", [CAP.DATA_WRITE])).toBe(true);
    expect(isMethodAllowed("update", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("updateMany", [CAP.DATA_READ])).toBe(false);
    expect(isMethodAllowed("remove", [CAP.DATA_READ])).toBe(false);
  });

  it("always allows the page to ask about itself", () => {
    expect(isMethodAllowed("me", [])).toBe(true);
  });

  it("never grants system capabilities to regular or legacy apps", () => {
    expect(ALL_CAPABILITIES).not.toContain(SYSTEM_CAP.READ);
    expect(ALL_CAPABILITIES).not.toContain(SYSTEM_CAP.MANAGE);
    expect(effectiveCapabilities(undefined)).not.toContain(SYSTEM_CAP.READ);
    expect(isMethodAllowed("systemQuery", undefined)).toBe(false);
    expect(isMethodAllowed("openManager", undefined)).toBe(false);
    expect(isMethodAllowed("openManager", [SYSTEM_CAP.MANAGE])).toBe(true);
  });

  it("denies methods it does not know", () => {
    // A method with no capability requirement must not be allowed by
    // default: every method added later would be open until it is mapped.
    expect(isMethodAllowed("unknownMethod", [])).toBe(false);
    expect(isMethodAllowed("unknownMethod", undefined)).toBe(false);
    expect(isMethodAllowed(undefined, [CAP.DATA_READ])).toBe(false);
  });
});
