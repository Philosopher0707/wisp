/**
 * Backend launch policy — pure, so it can be tested without Electron or a filesystem.
 *
 * Which Python starts the Wisp server, what environment it gets, and what the user is told when it does not come up.
 * `backend.ts` supplies the real filesystem and process; the tests supply fakes.
 *
 * Resolution order (first that exists wins):
 *   1. WISP_PYTHON            an explicit interpreter, for development and debugging
 *   2. bundled runtime         <Resources>/backend/python/bin/python3 — what a packaged Wisp.app ships (scripts/bundle-backend.sh)
 *   3. dev virtualenv          <repo>/.venv/bin/python, when running from the checkout
 *   4. system python3          last resort; works only if wisp is installed for it
 */

import * as path from 'node:path';

export type LaunchKind = 'env' | 'bundled' | 'dev-venv' | 'system';

export interface LaunchContext {
  env: Record<string, string | undefined>;
  /** process.cwd(): the desktop project directory in development, `/` for a launched .app */
  cwd: string;
  /** process.resourcesPath: the app's Resources directory when packaged */
  resourcesPath: string;
  isExecutable(p: string): boolean;
  isFile(p: string): boolean;
}

export interface Launch {
  python: string;
  /** Directory holding the `wisp` package when it is NOT installed into the interpreter; '' when it is. */
  root: string;
  kind: LaunchKind;
}

export function resolveLaunch(ctx: LaunchContext): Launch {
  const devRoot = path.resolve(ctx.cwd, '..');
  const sourceRoot = ctx.isFile(path.join(devRoot, 'wisp', '__init__.py')) ? devRoot : '';

  const explicit = (ctx.env.WISP_PYTHON ?? '').trim();
  if (explicit && ctx.isExecutable(explicit)) {
    return { python: explicit, root: sourceRoot, kind: 'env' };
  }

  const bundled = path.join(ctx.resourcesPath, 'backend', 'python', 'bin', 'python3');
  if (ctx.resourcesPath && ctx.isExecutable(bundled)) {
    // wisp is installed into the bundled interpreter's site-packages: no source root, nothing from the machine is needed.
    return { python: bundled, root: '', kind: 'bundled' };
  }

  const devVenv = path.join(devRoot, '.venv', 'bin', 'python');
  if (ctx.isExecutable(devVenv)) {
    return { python: devVenv, root: sourceRoot, kind: 'dev-venv' };
  }

  return { python: 'python3', root: sourceRoot, kind: 'system' };
}

/** The Python program that runs the server. The root is only put on sys.path when wisp is not installed into the interpreter. */
export function serverProgram(root: string, port: number): string {
  const lines = ['import sys'];
  if (root) lines.push(`sys.path.insert(0, ${JSON.stringify(root)})`);
  lines.push('from wisp.server import main', `main(host='127.0.0.1', port=${Math.trunc(port)}, no_auth=False)`);
  return lines.join('\n');
}

export interface SpawnEnvInput {
  base: Record<string, string | undefined>;
  launch: Launch;
  apiKey: string;
  workspace: string;
  corsOrigins: string[];
  /** Folders the user may switch the workspace into (the backend refuses anything else). */
  allowedWorkspaceRoots?: string[];
  jsonLogs?: boolean;
}

/** The environment for the server process. The bundled interpreter is sealed: it neither writes into the app nor reads user site-packages. */
export function spawnEnvironment(input: SpawnEnvInput): Record<string, string | undefined> {
  const env: Record<string, string | undefined> = {
    ...input.base,
    WISP_API_KEY: input.apiKey,
    WISP_WORKSPACE: input.workspace,
    WISP_CORS_ORIGINS: input.corsOrigins.join(','),
    PYTHONUNBUFFERED: '1',
  };
  // The backend's default is "only inside the current workspace", which makes choosing a project impossible. The app lets the
  // user pick a folder in a native dialog, so inside their home folder is allowed; an explicit setting from the environment wins.
  if (input.allowedWorkspaceRoots?.length && !input.base.WISP_ALLOWED_WORKSPACE_ROOTS) {
    env.WISP_ALLOWED_WORKSPACE_ROOTS = input.allowedWorkspaceRoots.join(',');
  }
  if (input.jsonLogs) env.WISP_JSON_LOGS = '1';
  if (input.launch.root) {
    env.PYTHONPATH = input.launch.root + path.delimiter + (input.base.PYTHONPATH ?? '');
  }
  if (input.launch.kind === 'bundled') {
    env.PYTHONDONTWRITEBYTECODE = '1'; // never touch the (signed) app bundle
    env.PYTHONNOUSERSITE = '1'; // a package in ~/Library/Python must not shadow the bundled one
    delete env.PYTHONHOME;
  }
  return env;
}

const TAIL_LINES = 30;
const TAIL_LINE_CHARS = 400;

/** The last lines the backend printed, for the error shown when it dies before it is ready. */
export class OutputTail {
  private readonly lines: string[] = [];

  push(chunk: string): void {
    for (const raw of chunk.split('\n')) {
      const line = raw.trimEnd();
      if (!line) continue;
      this.lines.push(line.length > TAIL_LINE_CHARS ? `${line.slice(0, TAIL_LINE_CHARS)}…` : line);
      if (this.lines.length > TAIL_LINES) this.lines.shift();
    }
  }

  text(count = 12): string {
    return this.lines.slice(-count).join('\n');
  }
}

export interface Exit {
  code: number | null;
  signal: string | null;
}

const KIND_LABEL: Record<LaunchKind, string> = {
  env: 'the interpreter named by WISP_PYTHON',
  bundled: 'the Python bundled with this app',
  'dev-venv': 'the development virtualenv',
  system: "the system's python3",
};

/** Why the server did not come up, in words a user can act on. */
export function describeStartupFailure(launch: Launch, tail: OutputTail, exit: Exit | null, spawnError: string | null): string {
  const who = `${KIND_LABEL[launch.kind]} (${launch.python})`;
  if (spawnError) {
    return `Could not start ${who}: ${spawnError}`;
  }
  const output = tail.text();
  const how = exit ? `exited (${exit.signal ? `signal ${exit.signal}` : `code ${exit.code}`}) before it was ready` : 'did not become ready in time';
  const hint = launch.kind === 'system'
    ? '\n\nThis app is using the system Python because no bundled or development runtime was found. Reinstall Wisp, or set WISP_PYTHON to an interpreter that has wisp installed.'
    : '';
  return `The Wisp backend, run with ${who}, ${how}.${output ? `\n\nLast output:\n${output}` : ''}${hint}`;
}
