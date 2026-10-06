#!/usr/bin/env node
// Launch the PACKAGED Wisp.app and check that it works as a self-contained app. Exit code 0 only if every check passes.
//
//   node scripts/verify-packaged.mjs [path/to/Wisp.app]     (default: release/mac-arm64/Wisp.app)
//
// It runs with a throwaway HOME so it never reads or writes the user's real Wisp data, and the backend is only asked for
// /api/health and /api/sessions: no model is ever called.
import { _electron as electron } from 'playwright';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(process.argv[2] ?? path.join(here, '..', 'release', 'mac-arm64', 'Wisp.app'));
const exe = path.join(app, 'Contents', 'MacOS', 'Wisp');
const shots = path.join(here, '..', 'build', 'verify');
mkdirSync(shots, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const listeningPid = (port) => {
  try {
    return execFileSync('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN', '-t'], { encoding: 'utf8' }).trim().split('\n')[0] || '';
  } catch {
    return '';
  }
};
const commandOf = (pid) => {
  try {
    return execFileSync('ps', ['-o', 'command=', '-p', pid], { encoding: 'utf8' }).trim();
  } catch {
    return '';
  }
};

if (!existsSync(exe)) {
  console.error(`no packaged app at ${exe}`);
  process.exit(2);
}

const home = mkdtempSync(path.join(tmpdir(), 'wisp-verify-home-'));
const errors = [];
let electronApp;
let port = 0;
try {
  electronApp = await electron.launch({
    executablePath: exe,
    env: { ...process.env, HOME: home, WISP_AUTO_UPDATE: 'false', ELECTRON_ENABLE_LOGGING: '1' },
    timeout: 60_000,
  });
  const win = await electronApp.firstWindow({ timeout: 60_000 });
  win.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  win.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`));
  await win.waitForLoadState('domcontentloaded');
  await sleep(2500);

  check('a window opened', (await electronApp.windows()).length >= 1, await win.title());
  await win.screenshot({ path: path.join(shots, 'window.png') });

  const info = await win.evaluate(() => window.wisp?.getBackendStatus?.());
  check('the app reports a managed backend', Boolean(info?.url) && info?.managed === true, info?.url ?? 'no status');
  port = Number(new URL(info.url).port);

  const auth = { Authorization: `Bearer ${info.apiKey}` };
  const health = await fetch(`${info.url}/api/health`, { headers: auth }).then((r) => r.json()).catch(() => null);
  check('/api/health answers ok with the key', health?.status === 'ok');
  const noKey = await fetch(`${info.url}/api/sessions`).then((r) => r.status).catch(() => 0);
  const badKey = await fetch(`${info.url}/api/sessions`, { headers: { Authorization: 'Bearer wrong' } }).then((r) => r.status).catch(() => 0);
  const goodKey = await fetch(`${info.url}/api/sessions`, { headers: auth }).then((r) => r.status).catch(() => 0);
  check('a protected endpoint refuses no key and a wrong key, accepts the right one', [noKey, badKey].every((s) => s === 401 || s === 403) && goodKey === 200,
    `none=${noKey} wrong=${badKey} right=${goodKey}`);

  // Features, not just startup: a stale bundled backend starts fine and silently lacks what the UI calls.
  const getJson = (p, init) => fetch(`${info.url}${p}`, { headers: auth, ...init }).then(async (r) => ({ status: r.status, body: await r.json().catch(() => null) })).catch(() => ({ status: 0, body: null }));
  const sessions = await getJson('/api/sessions');
  const rows = sessions.body?.sessions ?? [];
  check('/api/sessions tags every row with its source store', sessions.status === 200 && rows.every((r) => typeof r.source === 'string'), `${rows.length} rows`);
  const diff = await getJson('/api/git/diff');
  check('/api/git/diff exists and answers', diff.status === 200 && Array.isArray(diff.body?.files), `status=${diff.status}`);
  const imp = await getJson('/api/sessions/__no_such_session__/import', { method: 'POST' });
  check('session import route exists (404 from the handler, not from routing)', imp.status === 404 && /Session not found/.test(imp.body?.detail ?? ''), `status=${imp.status}`);
  const models = await getJson('/api/models/select', { method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' }, body: '{}' });
  check('model select route exists (422 on an empty body)', models.status === 422, `status=${models.status}`);

  const pid = listeningPid(port);
  const cmd = commandOf(pid);
  check('the backend is the Python bundled inside the app', cmd.includes('Wisp.app/Contents/Resources/backend/python/bin/python3') || cmd.includes(`${app}/Contents/Resources/backend/python`), cmd.slice(0, 90));
  check('it listens on loopback only', execFileSync('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN'], { encoding: 'utf8' }).includes('127.0.0.1:'));
  check('the renderer has no JavaScript errors', errors.length === 0, errors.slice(0, 2).join(' | '));
} catch (err) {
  check('the app launched and was driven', false, String(err).slice(0, 200));
} finally {
  if (electronApp) await electronApp.close().catch(() => {});
}

if (port) {
  let gone = false;
  for (let i = 0; i < 20 && !gone; i++) {
    await sleep(500);
    gone = listeningPid(port) === '';
  }
  check('closing the app stops the backend (no orphan)', gone);
}

const failed = results.filter((r) => !r.ok).length;
console.log(failed === 0 ? `\nall ${results.length} checks passed` : `\n${failed} of ${results.length} checks FAILED`);
process.exit(failed === 0 ? 0 : 1);
