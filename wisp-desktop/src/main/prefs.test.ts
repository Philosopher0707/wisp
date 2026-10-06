import { describe, it, expect } from 'vitest';
import { mkdtempSync, readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { parsePrefs, loadPrefs, savePrefs } from './prefs.js';

describe('prefs', () => {
  it('keeps only a plausible absolute workspace path', () => {
    expect(parsePrefs('{"workspace":"/Users/me/p"}')).toEqual({ workspace: '/Users/me/p' });
    expect(parsePrefs('{"workspace":"relative"}')).toEqual({});
    expect(parsePrefs('{"workspace":42,"other":1}')).toEqual({});
    expect(parsePrefs('not json')).toEqual({});
    expect(parsePrefs(`{"workspace":"/${'a'.repeat(2000)}"}`)).toEqual({});
  });

  it('round-trips through a file, atomically, with owner-only permissions', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'wisp-prefs-'));
    const file = path.join(dir, 'sub', 'prefs.json');
    savePrefs(file, { workspace: '/Users/me/proj' });
    expect(loadPrefs(file)).toEqual({ workspace: '/Users/me/proj' });
    expect(readdirSync(path.dirname(file))).toEqual(['prefs.json']); // no temp file left behind
    expect(statSync(file).mode & 0o777).toBe(0o600);
  });

  it('a missing or corrupt file is just empty prefs', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'wisp-prefs-'));
    expect(loadPrefs(path.join(dir, 'none.json'))).toEqual({});
    writeFileSync(path.join(dir, 'bad.json'), '{{{');
    expect(loadPrefs(path.join(dir, 'bad.json'))).toEqual({});
  });

  it('does not write unknown keys', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'wisp-prefs-'));
    const file = path.join(dir, 'p.json');
    savePrefs(file, { workspace: '/x', secret: 'nope' } as never);
    expect(JSON.parse(readFileSync(file, 'utf8'))).toEqual({ workspace: '/x' });
  });
});
