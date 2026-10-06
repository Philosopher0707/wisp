/** Pure parts of the in-app terminal, so they are tested without Electron or a real shell. */

export const MAX_SCROLLBACK_CHARS = 200_000;
export const MAX_INPUT_CHARS = 64 * 1024;

export function clampSize(cols: unknown, rows: unknown): { cols: number; rows: number } {
  const c = Number.isFinite(cols) ? Math.round(cols as number) : 80;
  const r = Number.isFinite(rows) ? Math.round(rows as number) : 24;
  return { cols: Math.min(500, Math.max(20, c)), rows: Math.min(200, Math.max(5, r)) };
}

/**
 * A tiny pty relay, run with Python (the app's own bundled interpreter): macOS `script` needs a real terminal on its stdin
 * and Node only has pipes, and a native pty module would have to be rebuilt per Electron release. It forks the user's login
 * shell onto a pseudo-terminal and relays bytes: stdin -> shell, shell -> stdout. Window-size changes arrive as "cols rows"
 * lines on fd 3. The shell path comes in as argv, never inside code.
 */
export const PTY_HELPER = `
import os, pty, sys, select, struct, fcntl, termios, signal
cols, rows, shell = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(shell, [shell, "-l"])
def resize(c, r):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", r, c, 0, 0))
    try:
        os.kill(pid, signal.SIGWINCH)
    except ProcessLookupError:
        pass
resize(cols, rows)
ctl = b""
code = 0
try:
    while True:
        ready, _, _ = select.select([0, fd, 3], [], [])
        if fd in ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                break
            os.write(1, data)
        if 0 in ready:
            data = os.read(0, 65536)
            if not data:
                os.kill(pid, signal.SIGHUP)
                break
            os.write(fd, data)
        if 3 in ready:
            more = os.read(3, 4096)
            if not more:
                os.kill(pid, signal.SIGHUP)
                break
            ctl += more
            while b"\\n" in ctl:
                line, ctl = ctl.split(b"\\n", 1)
                try:
                    c, r = line.split()
                    resize(int(c), int(r))
                except ValueError:
                    pass
finally:
    try:
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        pass
sys.exit(code if code >= 0 else 128 - code)
`;

export function shellLaunch(python: string, shell: string, cols: number, rows: number): { file: string; args: string[] } {
  const size = clampSize(cols, rows);
  return { file: python || '/usr/bin/python3', args: ['-I', '-c', PTY_HELPER, String(size.cols), String(size.rows), shell] };
}

/** The shell may only be one the machine lists in /etc/shells; anything else falls back to /bin/zsh. */
export function pickShell(envShell: string | undefined, allowed: string[]): string {
  const wanted = (envShell ?? '').trim();
  return wanted && allowed.includes(wanted) ? wanted : '/bin/zsh';
}

export function parseEtcShells(text: string): string[] {
  return text.split('\n').map((l) => l.trim()).filter((l) => l.startsWith('/'));
}

/** Environment for the user's shell: the app's own, minus anything that belongs to the app or its backend. */
export function shellEnvironment(base: Record<string, string | undefined>, cols: number, rows: number): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(base)) {
    if (v === undefined) continue;
    if (k === 'WISP_API_KEY' || k.startsWith('ELECTRON_') || k === 'NODE_OPTIONS') continue;
    env[k] = v;
  }
  const size = clampSize(cols, rows);
  env.TERM = 'xterm-256color';
  env.COLORTERM = 'truecolor';
  env.COLUMNS = String(size.cols);
  env.LINES = String(size.rows);
  return env;
}

/** Keeps the tail of the output so the terminal can be redrawn when the tab is reopened. */
export class Scrollback {
  private text = '';

  push(chunk: string): void {
    this.text += chunk;
    if (this.text.length > MAX_SCROLLBACK_CHARS) this.text = this.text.slice(this.text.length - MAX_SCROLLBACK_CHARS);
  }

  snapshot(): string {
    return this.text;
  }

  clear(): void {
    this.text = '';
  }
}
