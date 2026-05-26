"use strict";
/** Tests for config.ts */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const config_js_1 = require("../config.js");
(0, node_test_1.describe)("WispConfig", () => {
    (0, node_test_1.it)("has defaults", () => {
        const cfg = new config_js_1.WispConfig();
        node_assert_1.default.strictEqual(cfg.provider, "ollama");
        node_assert_1.default.strictEqual(cfg.model, "kimi-k2.6:cloud");
        node_assert_1.default.strictEqual(cfg.max_iterations, 30);
        node_assert_1.default.strictEqual(cfg.permission_mode, config_js_1.PermissionMode.AUTO_EDIT);
    });
    (0, node_test_1.it)("applies overrides", () => {
        const cfg = new config_js_1.WispConfig({ model: "llama3", max_iterations: 10 });
        node_assert_1.default.strictEqual(cfg.model, "llama3");
        node_assert_1.default.strictEqual(cfg.max_iterations, 10);
    });
    (0, node_test_1.it)("round-trips toJSON", () => {
        const cfg = new config_js_1.WispConfig();
        const json = cfg.toJSON();
        node_assert_1.default.strictEqual(json.model, cfg.model);
        node_assert_1.default.strictEqual(json.provider, cfg.provider);
    });
});
(0, node_test_1.describe)("validateConfig", () => {
    (0, node_test_1.it)("returns empty for valid config", () => {
        const errs = (0, config_js_1.validateConfig)({ model: "x", temperature: 0.5 });
        node_assert_1.default.deepStrictEqual(errs, []);
    });
    (0, node_test_1.it)("flags unknown keys", () => {
        const errs = (0, config_js_1.validateConfig)({ unknown_key: 1 });
        node_assert_1.default.ok(errs.some((e) => e.includes("unknown_key")));
    });
    (0, node_test_1.it)("flags type mismatch", () => {
        const errs = (0, config_js_1.validateConfig)({ temperature: "hot" });
        node_assert_1.default.ok(errs.some((e) => e.includes("temperature")));
    });
    (0, node_test_1.it)("flags out of range", () => {
        const errs = (0, config_js_1.validateConfig)({ temperature: 5.0 });
        node_assert_1.default.ok(errs.some((e) => e.includes("maximum")));
    });
});
//# sourceMappingURL=config.test.js.map