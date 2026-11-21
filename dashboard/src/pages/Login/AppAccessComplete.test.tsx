import { describe, expect, it } from "vitest";
import { readAppAccessParams } from "./AppAccessComplete";

describe("readAppAccessParams", () => {
  it("prefers the hash and keeps the code out of the query string", () => {
    expect(readAppAccessParams("#code=from-hash", "?code=from-query")).toEqual({
      code: "from-hash",
    });
  });

  it("falls back to the query for legacy links", () => {
    expect(readAppAccessParams("", "?code=legacy")).toEqual({
      code: "legacy",
    });
  });

  it("returns null when the link has no code", () => {
    expect(readAppAccessParams("", "")).toEqual({ code: null });
  });
});
