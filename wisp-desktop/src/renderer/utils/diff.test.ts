import { describe, it, expect } from 'vitest';
import { parseUnifiedDiff } from './diff.js';

const SAMPLE = [
  'diff --git a/a.txt b/a.txt',
  'index 1..2 100644',
  '--- a/a.txt',
  '+++ b/a.txt',
  '@@ -1,3 +1,3 @@',
  ' one',
  '-two',
  '+TWO',
  ' three',
  '',
].join('\n');

describe('parseUnifiedDiff', () => {
  it('counts added and removed lines, not the ---/+++ headers', () => {
    const d = parseUnifiedDiff(SAMPLE);
    expect(d.added).toBe(1);
    expect(d.removed).toBe(1);
  });

  it('numbers old and new lines independently', () => {
    const body = parseUnifiedDiff(SAMPLE).lines.filter((l) => l.kind !== 'meta' && l.kind !== 'hunk');
    expect(body.map((l) => [l.kind, l.oldNo, l.newNo])).toEqual([
      ['ctx', 1, 1],
      ['del', 2, null],
      ['add', null, 2],
      ['ctx', 3, 3],
    ]);
  });

  it('does not invent a context line for the trailing newline', () => {
    const lines = parseUnifiedDiff(SAMPLE).lines;
    expect(lines[lines.length - 1].text).toBe('three');
  });

  it('a "-- " or "++ " content line inside a hunk is content, not a header', () => {
    const d = parseUnifiedDiff('@@ -1 +1 @@\n--- removed dashes\n+++ added pluses\n');
    expect(d.removed).toBe(1);
    expect(d.added).toBe(1);
  });

  it('keeps the no-newline marker as meta', () => {
    const d = parseUnifiedDiff('@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n');
    expect(d.lines.some((l) => l.kind === 'meta' && l.text.startsWith('\\'))).toBe(true);
    expect(d.added).toBe(1);
  });

  it('empty input has no lines', () => {
    expect(parseUnifiedDiff('')).toEqual({ lines: [], added: 0, removed: 0 });
  });
});
