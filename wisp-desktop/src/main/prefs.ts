import { readFileSync, writeFileSync, renameSync, mkdirSync } from 'node:fs';
import path from 'node:path';

/** The few settings the app itself remembers across launches, kept in the main process (not renderer localStorage, which
 *  Chromium flushes lazily and users can clear). Pure parse/serialise plus an atomic write, so it is tested without Electron. */
export interface Prefs {
  workspace?: string;
}

export function parsePrefs(text: string): Prefs {
  try {
    const raw = JSON.parse(text) as Record<string, unknown>;
    const out: Prefs = {};
    if (typeof raw.workspace === 'string' && raw.workspace.startsWith('/') && raw.workspace.length < 1024) out.workspace = raw.workspace;
    return out;
  } catch {
    return {};
  }
}

export function loadPrefs(file: string): Prefs {
  try {
    return parsePrefs(readFileSync(file, 'utf8'));
  } catch {
    return {};
  }
}

/** Forget the remembered project (the folder is gone or no longer allowed). */
export function withoutWorkspace(prefs: Prefs): Prefs {
  const { workspace: _dropped, ...rest } = prefs;
  return rest;
}

export function savePrefs(file: string, prefs: Prefs): void {
  mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(parsePrefs(JSON.stringify(prefs)), null, 2), { mode: 0o600 });
  renameSync(tmp, file); // atomic: a crash mid-write never leaves a half-written file
}
