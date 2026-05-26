/** Tests for config.ts */

import { describe, it } from "node:test";
import assert from "node:assert";
import { WispConfig, validateConfig, PermissionMode } from "../src/config.js";

describe("WispConfig", () => {
  it("has defaults", () => {
    const cfg = new WispConfig();
    assert.strictEqual(cfg.provider, "ollama");
    assert.strictEqual(cfg.model, "kimi-k2.6:cloud");
    assert.strictEqual(cfg.max_iterations, 30);
    assert.strictEqual(cfg.permission_mode, PermissionMode.AUTO_EDIT);
  });

  it("applies overrides", () => {
    const cfg = new WispConfig({ model: "llama3", max_iterations: 10 });
    assert.strictEqual(cfg.model, "llama3");
    assert.strictEqual(cfg.max_iterations, 10);
  });

  it("round-trips toJSON", () => {
    const cfg = new WispConfig();
    const json = cfg.toJSON();
    assert.strictEqual(json.model, cfg.model);
    assert.strictEqual(json.provider, cfg.provider);
  });
});

describe("validateConfig", () => {
  it("returns empty for valid config", () => {
    const errs = validateConfig({ model: "x", temperature: 0.5 });
    assert.deepStrictEqual(errs, []);
  });

  it("flags unknown keys", () => {
    const errs = validateConfig({ unknown_key: 1 });
    assert.ok(errs.some((e: string) => e.includes("unknown_key")));
  });

  it("flags type mismatch", () => {
    const errs = validateConfig({ temperature: "hot" });
    assert.ok(errs.some((e: string) => e.includes("temperature")));
  });

  it("flags out of range", () => {
    const errs = validateConfig({ temperature: 5.0 });
    assert.ok(errs.some((e: string) => e.includes("maximum")));
  });
});
