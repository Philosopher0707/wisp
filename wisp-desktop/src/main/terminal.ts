import { ipcMain, type BrowserWindow } from 'electron';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { clampSize, MAX_INPUT_CHARS, parseEtcShells, pickShell, Scrollback, shellEnvironment, shellLaunch } from './terminal-policy.js';
import { getBackendPython, getBackendStatus } from './backend.js';

/**
 * The dock's Terminal tab: the user's own login shell on a pty, running on the host in the workspace. This is the person
 * typing in their own app, not the agent, so it is deliberately not routed through the agent's tool policy (that policy
 * is unchanged). It is reachable only from the main window's renderer (sender check), is never handed the backend key,
 * and dies with the window.
 */
export function registerTerminal(win: BrowserWindow): void {
  let child: ChildProcessWithoutNullStreams | null = null;
  const scrollback = new Scrollback();
  let cwd = homedir();
  let shellName = '';

  const send = (channel: string, payload?: unknown) => {
    if (!win.isDestroyed()) win.webContents.send(channel, payload);
  };
  const trusted = (e: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent) => e.sender === win.webContents;

  const resize = (cols: number, rows: number) => {
    const size = clampSize(cols, rows);
    const ctl = child?.stdio[3] as NodeJS.WritableStream | undefined;
    ctl?.write(`${size.cols} ${size.rows}\n`);
  };

  const stop = () => {
    if (child && !child.killed) child.kill('SIGHUP');
    child = null;
  };

  const start = (cols: number, rows: number) => {
    const workspace = getBackendStatus()?.workspace;
    cwd = workspace && existsSync(workspace) ? workspace : homedir();
    const shells = existsSync('/etc/shells') ? parseEtcShells(readFileSync('/etc/shells', 'utf8')) : [];
    const shell = pickShell(process.env.SHELL, shells);
    shellName = shell;
    const { file, args } = shellLaunch(getBackendPython(), shell, cols, rows);
    // fd 3 carries window-size changes to the helper; stdout carries the shell's terminal output (stderr is the helper's own).
    const proc = spawn(file, args, { cwd, env: shellEnvironment(process.env, cols, rows), stdio: ['pipe', 'pipe', 'pipe', 'pipe'] }) as unknown as ChildProcessWithoutNullStreams;
    proc.stdout.setEncoding('utf8');
    proc.stdout.on('data', (d: string) => {
      scrollback.push(d);
      send('terminal:data', d);
    });
    proc.stderr.on('data', () => {});
    proc.stdin.on('error', () => {});
    proc.on('error', (err) => {
      if (child === proc) child = null;
      send('terminal:exit', { code: null, error: err.message });
    });
    proc.on('exit', (code, signal) => {
      if (child === proc) child = null;
      send('terminal:exit', { code, signal });
    });
    child = proc;
    return { shell, cwd };
  };

  ipcMain.handle('terminal:start', (event, cols: number, rows: number) => {
    if (!trusted(event)) return { ok: false };
    if (child) return { ok: true, resumed: true, cwd, shell: shellName, buffer: scrollback.snapshot() };
    scrollback.clear();
    const size = clampSize(cols, rows);
    try {
      return { ok: true, resumed: false, ...start(size.cols, size.rows), buffer: '' };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  });

  ipcMain.on('terminal:input', (event, data: unknown) => {
    if (!trusted(event) || !child || typeof data !== 'string' || data.length > MAX_INPUT_CHARS) return;
    child.stdin.write(data);
  });

  ipcMain.on('terminal:resize', (event, cols: number, rows: number) => {
    if (trusted(event) && child) resize(cols, rows);
  });

  ipcMain.on('terminal:restart', (event) => {
    if (!trusted(event)) return;
    stop();
    scrollback.clear();
  });

  win.on('closed', () => {
    stop();
    ipcMain.removeHandler('terminal:start');
    for (const ch of ['terminal:input', 'terminal:resize', 'terminal:restart']) ipcMain.removeAllListeners(ch);
  });
}
