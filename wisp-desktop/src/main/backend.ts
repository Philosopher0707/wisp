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
import {
  OutputTail,
  describeStartupFailure,
  resolveLaunch,
  serverProgram,
  spawnEnvironment,
  type Exit,
  type LaunchContext,
} from './backend-launch.js';

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
let backendPython = '';

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
  /** Returns true once the backend process is gone, so a crash is reported at once instead of after the whole timeout. */
  hasDied: () => boolean = () => false,
): Promise<void> {
  const deadline = Date.now() + timeoutSeconds * 1000;
  while (Date.now() < deadline) {
    if (hasDied()) throw new Error('Backend process exited before it became healthy');
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

/** The real filesystem for the pure launch policy in backend-launch.ts. */
function realLaunchContext(): LaunchContext {
  const { accessSync, statSync, constants } = require('node:fs') as typeof import('node:fs');
  return {
    env: process.env,
    cwd: process.cwd(),
    resourcesPath: process.resourcesPath ?? '',
    isExecutable: (p) => {
      try {
        accessSync(p, constants.X_OK);
        return statSync(p).isFile();
      } catch {
        return false;
      }
    },
    isFile: (p) => {
      try {
        return statSync(p).isFile();
      } catch {
        return false;
      }
    },
  };
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

  const launch = resolveLaunch(realLaunchContext());
  backendPython = launch.python;
  const env = spawnEnvironment({
    base: process.env,
    launch,
    apiKey,
    workspace,
    corsOrigins: opts.corsOrigins || ['http://localhost', 'http://127.0.0.1'],
    jsonLogs: opts.jsonLogs,
  });

  // Ensure workspace exists
  const { mkdirSync } = require('node:fs');
  try {
    mkdirSync(workspace, { recursive: true });
  } catch {
    /* already exists */
  }

  // Spawn the server with `-c` so we do not depend on a `wisp` CLI entrypoint being installed anywhere.
  const args: string[] = ['-c', serverProgram(launch.root, port)];

  safeLog('log', '[backend] Spawning:', launch.python, `(${launch.kind})`);
  safeLog('log', '[backend] Port:', port);
  safeLog('log', '[backend] Workspace:', workspace);

  // What the backend says is kept (last lines only) so a failed start can show the real cause.
  const tail = new OutputTail();
  let exit: Exit | null = null;
  let spawnError: string | null = null;

  backendProcess = spawn(launch.python, args, {
    env: env as NodeJS.ProcessEnv,
    detached: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  backendProcess.stdout?.on('data', (chunk: Buffer) => {
    const text = chunk.toString();
    tail.push(text);
    for (const line of text.trimEnd().split('\n')) {
      if (line) safeLog('log', '[backend]', line);
    }
  });
  backendProcess.stderr?.on('data', (chunk: Buffer) => {
    const text = chunk.toString();
    tail.push(text);
    for (const line of text.trimEnd().split('\n')) {
      if (line) safeLog('error', '[backend]', line);
    }
  });

  backendProcess.on('error', (err) => {
    spawnError = err.message;
    safeLog('error', '[backend] Process error:', err.message);
  });

  backendProcess.on('exit', (code, signal) => {
    exit = { code, signal };
    safeLog('log', `[backend] Exited code=${code} signal=${signal}`);
    backendProcess = null;
    backendInfo = null;
  });

  // Wait for health; a backend that has already died is reported at once, with what it printed.
  try {
    await waitForHealthy(url, apiKey, timeout, () => exit !== null || spawnError !== null);
  } catch (err) {
    killBackend();
    throw new Error(describeStartupFailure(launch, tail, exit, spawnError), { cause: err });
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

/** The interpreter the backend runs on (main process only; the in-app terminal reuses it for its pty helper). */
export function getBackendPython(): string {
  return backendPython;
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
