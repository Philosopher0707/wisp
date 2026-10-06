import { describe, it, expect } from 'vitest';
import { spawn } from 'node:child_process';
import { clampSize, shellLaunch, PTY_HELPER, pickShell, parseEtcShells, shellEnvironment, Scrollback, MAX_SCROLLBACK_CHARS } from './terminal-policy.js';

describe('terminal policy', () => {
  it('clamps the size and survives junk', () => {
    expect(clampSize(0, 0)).toEqual({ cols: 20, rows: 5 });
    expect(clampSize(9999, 9999)).toEqual({ cols: 500, rows: 200 });
    expect(clampSize('x', undefined)).toEqual({ cols: 80, rows: 24 });
  });

  it('passes the shell as argv, never inside the helper code', () => {
    const l = shellLaunch('/usr/bin/python3', '/bin/zsh; rm -rf ~', 100, 30);
    expect(l.file).toBe('/usr/bin/python3');
    expect(l.args).toEqual(['-I', '-c', PTY_HELPER, '100', '30', '/bin/zsh; rm -rf ~']);
    expect(PTY_HELPER).not.toContain('rm -rf');
  });

  it('only accepts a listed shell', () => {
    const shells = parseEtcShells('# comment\n/bin/zsh\n/bin/bash\n\n');
    expect(shells).toEqual(['/bin/zsh', '/bin/bash']);
    expect(pickShell('/bin/bash', shells)).toBe('/bin/bash');
    expect(pickShell('/tmp/evil', shells)).toBe('/bin/zsh');
    expect(pickShell(undefined, shells)).toBe('/bin/zsh');
  });

  it('keeps the backend key and Electron variables out of the shell', () => {
    const env = shellEnvironment({ HOME: '/h', WISP_API_KEY: 'secret', ELECTRON_RUN_AS_NODE: '1', NODE_OPTIONS: '--x', PATH: '/bin' }, 90, 20);
    expect(env.WISP_API_KEY).toBeUndefined();
    expect(env.ELECTRON_RUN_AS_NODE).toBeUndefined();
    expect(env.NODE_OPTIONS).toBeUndefined();
    expect(env).toMatchObject({ HOME: '/h', PATH: '/bin', TERM: 'xterm-256color', COLUMNS: '90', LINES: '20' });
  });

  it('scrollback keeps only the tail', () => {
    const s = new Scrollback();
    s.push('a'.repeat(MAX_SCROLLBACK_CHARS));
    s.push('TAIL');
    expect(s.snapshot().length).toBe(MAX_SCROLLBACK_CHARS);
    expect(s.snapshot().endsWith('TAIL')).toBe(true);
    s.clear();
    expect(s.snapshot()).toBe('');
  });

  // The helper is real code: run it against a real shell and a real pty.
  it('the pty helper runs a real shell: tty, resize, exit status', async () => {
    const proc = spawn('python3', ['-I', '-c', PTY_HELPER, '90', '20', '/bin/sh'], { stdio: ['pipe', 'pipe', 'pipe', 'pipe'] });
    let out = '';
    proc.stdout.setEncoding('utf8');
    proc.stdout.on('data', (d: string) => (out += d));
    const wait = async (needle: string) => {
      for (let i = 0; i < 100 && !out.includes(needle); i++) await new Promise((r) => setTimeout(r, 50));
      return out.includes(needle);
    };
    proc.stdin.write('[ -t 0 ] && echo TTY_$((6*7))\n');
    expect(await wait('TTY_42')).toBe(true);
    (proc.stdio[3] as NodeJS.WritableStream).write('120 33\n');
    await new Promise((r) => setTimeout(r, 200));
    proc.stdin.write('stty size\n');
    expect(await wait('33 120')).toBe(true);
    const exit = new Promise<number | null>((r) => proc.on('exit', (c) => r(c)));
    proc.stdin.write('exit 7\n');
    expect(await exit).toBe(7);
  });
});
