import { describe, expect, it } from "vitest";

import { SYSTEM_APP_SDK } from "./systemSdk";

describe("system app SDK", () => {
  it("speaks the host bridge protocol with name/args and url", () => {
    expect(SYSTEM_APP_SDK).toMatch(/call\("systemQuery",\s*\{\s*name:\s*key/);
    expect(SYSTEM_APP_SDK).toMatch(/call\("systemAction",\s*\{\s*name:\s*key/);
    expect(SYSTEM_APP_SDK).toMatch(/call\("openConsole",\s*\{\s*url:/);
    expect(SYSTEM_APP_SDK).toContain('source: "octop-app"');
    expect(SYSTEM_APP_SDK).not.toContain("octopApp");
  });
});
