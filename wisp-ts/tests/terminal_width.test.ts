/** Tests for terminal_width.ts */

import { describe, it } from "node:test";
import assert from "node:assert";
import {
  displayWidth,
  stripAnsi,
  wrapTextWide,
  padRight,
  center,
  BoxChars,
  OutputMode,
  truncate,
} from "../src/terminal_width.js";

describe("displayWidth", () => {
  it("measures ASCII correctly", () => {
    assert.strictEqual(displayWidth("hello"), 5);
  });

  it("measures CJK as 2", () => {
    assert.strictEqual(displayWidth("你好"), 4);
  });

  it("ignores ANSI", () => {
    assert.strictEqual(displayWidth("\u001b[32mok\u001b[0m"), 2);
  });
});

describe("stripAnsi", () => {
  it("removes color codes", () => {
    assert.strictEqual(stripAnsi("\u001b[32mok\u001b[0m"), "ok");
  });
});

describe("wrapTextWide", () => {
  it("wraps text", () => {
    const lines = wrapTextWide("hello world foo bar", 10);
    assert.ok(lines.length > 1);
    assert.ok(lines.every((l: string) => displayWidth(l) <= 10 || l === ""));
  });

  it("handles empty", () => {
    assert.deepStrictEqual(wrapTextWide("", 10), [""]);
  });
});

describe("padRight", () => {
  it("pads to target", () => {
    assert.strictEqual(padRight("hi", 5), "hi   ");
  });

  it("no-op if already wide enough", () => {
    assert.strictEqual(padRight("hello", 3), "hello");
  });
});

describe("center", () => {
  it("centers text", () => {
    const result = center("hi", 6);
    assert.strictEqual(displayWidth(result), 6);
    assert.ok(result.includes("hi"));
  });
});

describe("BoxChars", () => {
  it("unicode mode draws corners", () => {
    const box = new BoxChars(OutputMode.UNICODE);
    assert.strictEqual(box.tl, "┌");
    assert.strictEqual(box.br, "┘");
  });

  it("ascii mode draws plus", () => {
    const box = new BoxChars(OutputMode.ASCII);
    assert.strictEqual(box.tl, "+");
    assert.strictEqual(box.hz, "-");
  });

  it("minimal mode is empty", () => {
    const box = new BoxChars(OutputMode.MINIMAL);
    assert.strictEqual(box.tl, "");
  });

  it("top with title", () => {
    const box = new BoxChars(OutputMode.UNICODE);
    const top = box.top(20, "Title");
    assert.ok(top.includes("Title"));
  });

  it("line wraps content", () => {
    const box = new BoxChars(OutputMode.UNICODE);
    const line = box.line(10, "hi");
    assert.ok(line.includes("hi"));
  });
});

describe("truncate", () => {
  it("does not truncate short text", () => {
    assert.strictEqual(truncate("hi", 5), "hi");
  });

  it("truncates long text", () => {
    assert.strictEqual(truncate("hello world", 8), "hello...");
  });
});
