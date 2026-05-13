/**
 * Backend Launcher — manages the Wisp Python server lifecycle.
 *
 * Spawns the FastAPI backend as a child process, finds an available port,
 * waits for it to be healthy, then exposes the URL + API key to the renderer.
 * On app quit the backend is gracefully terminated.
 */

import { spawn, ChildProcess } from 'node:child_process';
import * as net from 'node:net';
import * as path from 'node:path';
import * as os from 'node:os';
import { app } from 'electron';

export interface BackendInfo {
  /** e.g. http://localhost:8473 */
  url: string;
  /** Pre-shared auth key */
  apiKey: string;
  /** Absolute path to the workspace directory */
  workspace: string;
  /** Whether this backend was spawned by the desktop app (true) or externally managed (false) */
  managed: boolean;
}

interface BackendOptions {
  /** Preferred port (0 = auto) */
  preferredPort?: number;
  /** Fixed API key (default = random 32-byte) */
  apiKey?: string;
  /** Workspace directory (default = ~/.wisp/workspace) */
  workspace?: string;
  /** CORS origins (default = allow all localhost) */
  corsOrigins?: string[];
  /** Enable JSON structured logs */
  jsonLogs?: boolean;
  /** Seconds to wait for health before giving up (default 30) */
  healthTimeoutSeconds?: number;
}

let backendProcess: ChildProcess | null = null;
let backendInfo: BackendInfo | null = null;

/** Log helper that silently swallows EPIPE when process.stdout is a broken pipe */
function safeLog(stream: 'log' | 'warn' | 'error', prefix: string, ...values: unknown[]) {
  try {
    const method = stream === 'log' ? console.log : stream === 'warn' ? console.warn : console.error;
    method(prefix, ...values);
  } catch {
    /* process.stdout may be a broken pipe when launched from GUI */
  }
}

/** Generate a secure random API key */
function generateApiKey(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.';
  let key = '';
  for (let i = 0; i < 43; i++) {
    key += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return key;
}

/** Find an available TCP port */
function findFreePort(preferred = 0): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', (err) => reject(err));
    server.listen(preferred, '127.0.0.1', () => {
      const addr = server.address();
      const port = typeof addr === 'string' ? 0 : addr?.port ?? 0;
      server.close(() => resolve(port));
    });
  });
}

/** Poll /api/health until it responds 200 */
async function waitForHealthy(
  url: string,
  apiKey: string,
  timeoutSeconds: number,
): Promise<void> {
  const deadline = Date.now() + timeoutSeconds * 1000;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(`${url}/api/health`, {
        headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
      });
      if (resp.ok) {
        const data = (await resp.json()) as { status?: string };
        if (data.status === 'ok') return;
      }
    } catch {
      /* not ready yet */
    }
    await sleep(300);
  }
  throw new Error(`Backend health check timed out after ${timeoutSeconds}s`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Resolve the correct Python interpreter for the backend */
function resolvePython(): string {
  // 1. In development, prefer the virtualenv inside the backend repo
  const devVenv = path.resolve(
    process.cwd(),
    '..', '.venv', 'bin', 'python',
  );
  if (process.platform === 'darwin' || process.platform === 'linux') {
    try {
      const { accessSync, constants } = require('node:fs');
      accessSync(devVenv, constants.X_OK);
      return devVenv;
    } catch {
      /* not executable */
    }
  }

  // 2. Packaged app: look in bundled resources
  const resourcesVenv = path.join(
    process.resourcesPath,
    'backend', '.venv', 'bin', 'python',
  );
  try {
    const { accessSync, constants } = require('node:fs');
    accessSync(resourcesVenv, constants.X_OK);
    return resourcesVenv;
  } catch {
    /* not bundled */
  }

  // 3. Fallback to system Python (requires wisp installed globally)
  return 'python3';
}

/** Resolve the backend source root (for PYTHONPATH when using dev venv) */
function resolveBackendRoot(): string {
  // In dev: parent directory of wisp-desktop (where the 'wisp' package lives)
  // e.g. /Users/philosopher/Documents/wisp/wisp-desktop/.. => /Users/philosopher/Documents/wisp
  const devRoot = path.resolve(process.cwd(), '..');
  try {
    const { statSync } = require('node:fs');
    if (statSync(path.join(devRoot, 'wisp', '__init__.py')).isFile()) return devRoot;
  } catch {
    /* doesn't exist */
  }

  // Packaged: bundled inside app.asar or Resources
  const bundledRoot = path.join(process.resourcesPath, 'backend');
  try {
    const { statSync } = require('node:fs');
    if (statSync(bundledRoot).isDirectory()) return bundledRoot;
  } catch {
    /* doesn't exist */
  }

  return '';
}

/**
 * Start the Wisp backend server as a managed child process.
 * Returns the URL and API key once the health endpoint is live.
 */
export async function startBackend(opts: BackendOptions = {}): Promise<BackendInfo> {
  if (backendProcess) {
    // Already running
    if (backendInfo) return backendInfo;
    throw new Error('Backend spawn in progress');
  }

  const port = await findFreePort(opts.preferredPort ?? 0);
  const apiKey = opts.apiKey || generateApiKey();
  const workspace = opts.workspace || path.join(os.homedir(), '.wisp', 'workspace');
  const timeout = opts.healthTimeoutSeconds ?? 30;
  const url = `http://localhost:${port}`;

  const python = resolvePython();
  const backendRoot = resolveBackendRoot();
  const wispPkg = path.join(backendRoot, 'wisp');

  // Build environment
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    WISP_API_KEY: apiKey,
    WISP_WORKSPACE: workspace,
    WISP_CORS_ORIGINS: (opts.corsOrigins || [
      'http://localhost',
      'http://127.0.0.1',
    ]).join(','),
  };
  if (opts.jsonLogs) {
    env.WISP_JSON_LOGS = '1';
  }

  // Ensure workspace exists
  const { mkdirSync } = require('node:fs');
  try {
    mkdirSync(workspace, { recursive: true });
  } catch {
    /* already exists */
  }

  // Spawn: wisp.server.main via uvicorn or direct Python
  // We use the venv Python with -c to import and call main() directly
  // so we don't rely on the `wisp` CLI entrypoint being installed globally.
  const args: string[] = [
    '-c',
    `
import sys, os
sys.path.insert(0, ${JSON.stringify(backendRoot)})
if ${JSON.stringify(wispPkg)} and os.path.isdir(${JSON.stringify(wispPkg)}):
    os.environ['PYTHONPATH'] = ${JSON.stringify(backendRoot)} + os.pathsep + os.environ.get('PYTHONPATH', '')
from wisp.server import main
main(host='127.0.0.1', port=${port}, no_auth=False)
`,
  ];

  safeLog('log', '[backend] Spawning:', python, '<inline>');
  safeLog('log', '[backend] Port:', port);
  safeLog('log', '[backend] Workspace:', workspace);

  backendProcess = spawn(python, args, {
    env,
    detached: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  backendProcess.stdout?.on('data', (chunk: Buffer) => {
    const lines = chunk.toString().trimEnd().split('\n');
    for (const line of lines) {
      if (line) safeLog('log', '[backend]', line);
    }
  });
  backendProcess.stderr?.on('data', (chunk: Buffer) => {
    const lines = chunk.toString().trimEnd().split('\n');
    for (const line of lines) {
      if (line) safeLog('error', '[backend]', line);
    }
  });

  backendProcess.on('error', (err) => {
    safeLog('error', '[backend] Process error:', err.message);
  });

  backendProcess.on('exit', (code, signal) => {
    safeLog('log', `[backend] Exited code=${code} signal=${signal}`);
    backendProcess = null;
    backendInfo = null;
  });

  // Wait for health
  try {
    await waitForHealthy(url, apiKey, timeout);
  } catch (err) {
    // Clean up on failure
    killBackend();
    throw err;
  }

  backendInfo = { url, apiKey, workspace, managed: true };
  safeLog('log', '[backend] Healthy — ready for connections');
  return backendInfo;
}

/** Kill the managed backend process */
export function killBackend(): void {
  if (!backendProcess) return;

  const proc = backendProcess;
  backendProcess = null;
  backendInfo = null;

  // Try graceful SIGTERM first
  if (process.platform === 'win32') {
    proc.kill('SIGTERM');
  } else {
    proc.kill('SIGTERM');
  }

  // Force kill after 5s
  const forceTimer = setTimeout(() => {
    if (!proc.killed) {
      safeLog('warn', '[backend] Force killing with SIGKILL');
      proc.kill('SIGKILL');
    }
  }, 5000);

  proc.on('exit', () => clearTimeout(forceTimer));
}

/** Return current backend info (or null if not running) */
export function getBackendStatus(): BackendInfo | null {
  return backendInfo;
}

/** Register app quit handler so backend always dies with the app */
export function registerBackendCleanup(): void {
  app.on('before-quit', () => {
    killBackend();
  });
  app.on('quit', () => {
    killBackend();
  });
}
