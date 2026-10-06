import { describe, expect, it } from 'vitest';
import {
  OutputTail,
  describeStartupFailure,
  resolveLaunch,
  serverProgram,
  spawnEnvironment,
  type Launch,
  type LaunchContext,
} from './backend-launch';

/** A fake filesystem: `exec` paths are executable files, `files` are plain files. */
function ctx(over: Partial<LaunchContext> & { exec?: string[]; files?: string[] } = {}): LaunchContext {
  const exec = new Set(over.exec ?? []);
  const files = new Set([...(over.files ?? []), ...exec]);
  return {
    env: over.env ?? {},
    cwd: over.cwd ?? '/',
    resourcesPath: over.resourcesPath ?? '',
    isExecutable: (p) => exec.has(p),
    isFile: (p) => files.has(p),
  };
}

const BUNDLED = '/Applications/Wisp.app/Contents/Resources/backend/python/bin/python3';
const RES = '/Applications/Wisp.app/Contents/Resources';

describe('resolveLaunch', () => {
  it('uses the bundled interpreter in a packaged app, with nothing taken from the machine', () => {
    const l = resolveLaunch(ctx({ resourcesPath: RES, cwd: '/', exec: [BUNDLED] }));
    expect(l).toEqual({ python: BUNDLED, root: '', kind: 'bundled' });
  });

  it('prefers an explicit WISP_PYTHON over everything else', () => {
    const l = resolveLaunch(ctx({ resourcesPath: RES, env: { WISP_PYTHON: '/opt/py/bin/python' }, exec: [BUNDLED, '/opt/py/bin/python'] }));
    expect(l.kind).toBe('env');
    expect(l.python).toBe('/opt/py/bin/python');
  });

  it('ignores a WISP_PYTHON that is not executable instead of failing on it', () => {
    const l = resolveLaunch(ctx({ resourcesPath: RES, env: { WISP_PYTHON: '/nope/python' }, exec: [BUNDLED] }));
    expect(l.kind).toBe('bundled');
  });

  it('uses the checkout virtualenv and the checkout as the source root when run from wisp-desktop', () => {
    const l = resolveLaunch(ctx({
      cwd: '/Users/me/wisp/wisp-desktop', resourcesPath: '/x/Electron.app/Contents/Resources',
      exec: ['/Users/me/wisp/.venv/bin/python'], files: ['/Users/me/wisp/wisp/__init__.py'],
    }));
    expect(l).toEqual({ python: '/Users/me/wisp/.venv/bin/python', root: '/Users/me/wisp', kind: 'dev-venv' });
  });

  it('a bundled runtime beats a development virtualenv when both exist', () => {
    const l = resolveLaunch(ctx({ resourcesPath: RES, cwd: '/Users/me/wisp/wisp-desktop', exec: [BUNDLED, '/Users/me/wisp/.venv/bin/python'] }));
    expect(l.kind).toBe('bundled');
  });

  it('falls back to the system python3 and says so', () => {
    const l = resolveLaunch(ctx({ resourcesPath: RES }));
    expect(l).toEqual({ python: 'python3', root: '', kind: 'system' });
  });

  it('does not look for a bundled runtime when there is no resources path (unpackaged)', () => {
    expect(resolveLaunch(ctx({ resourcesPath: '', exec: ['backend/python/bin/python3', '/backend/python/bin/python3'] })).kind).toBe('system');
  });
});

describe('serverProgram', () => {
  it('puts no path on sys.path when wisp is installed into the interpreter', () => {
    const p = serverProgram('', 8473);
    expect(p).not.toContain('sys.path.insert');
    expect(p).toContain("from wisp.server import main");
    expect(p).toContain("main(host='127.0.0.1', port=8473, no_auth=False)");
  });

  it('adds the source root once, quoted safely', () => {
    const p = serverProgram('/Users/o"brien/wisp', 9000);
    expect(p.match(/sys\.path\.insert/g)).toHaveLength(1);
    expect(p).toContain(JSON.stringify('/Users/o"brien/wisp'));
  });

  it('only ever interpolates an integer port', () => {
    expect(serverProgram('', 80.9 as number)).toContain('port=80,');
  });
});

describe('spawnEnvironment', () => {
  const base = { PATH: '/usr/bin', HOME: '/Users/me', PYTHONHOME: '/evil', PYTHONPATH: '/already' };
  const input = (launch: Launch) => ({ base, launch, apiKey: 'k', workspace: '/w', corsOrigins: ['http://localhost', 'http://127.0.0.1'] });

  it('seals the bundled interpreter: no bytecode written into the app, no user site-packages, no inherited PYTHONHOME', () => {
    const env = spawnEnvironment(input({ python: BUNDLED, root: '', kind: 'bundled' }));
    expect(env.PYTHONDONTWRITEBYTECODE).toBe('1');
    expect(env.PYTHONNOUSERSITE).toBe('1');
    expect(env.PYTHONHOME).toBeUndefined();
    expect(env.PYTHONPATH).toBe('/already');
  });

  it('does not seal a development interpreter, and prepends the source root to PYTHONPATH', () => {
    const env = spawnEnvironment(input({ python: '/v/python', root: '/Users/me/wisp', kind: 'dev-venv' }));
    expect(env.PYTHONDONTWRITEBYTECODE).toBeUndefined();
    expect(env.PYTHONPATH?.startsWith('/Users/me/wisp')).toBe(true);
    expect(env.PYTHONPATH).toContain('/already');
  });

  it('always carries the key, workspace and CORS origins, and never leaks them into the input', () => {
    const env = spawnEnvironment(input({ python: 'python3', root: '', kind: 'system' }));
    expect(env).toMatchObject({ WISP_API_KEY: 'k', WISP_WORKSPACE: '/w', WISP_CORS_ORIGINS: 'http://localhost,http://127.0.0.1', PYTHONUNBUFFERED: '1' });
    expect((base as Record<string, string>).WISP_API_KEY).toBeUndefined();
  });

  it('lets the workspace be switched within the home folder, unless the environment already says otherwise', () => {
    const launch: Launch = { python: 'python3', root: '', kind: 'system' };
    expect(spawnEnvironment({ ...input(launch), allowedWorkspaceRoots: ['/Users/me'] }).WISP_ALLOWED_WORKSPACE_ROOTS).toBe('/Users/me');
    const custom = spawnEnvironment({ ...input(launch), base: { ...base, WISP_ALLOWED_WORKSPACE_ROOTS: '/srv/only' }, allowedWorkspaceRoots: ['/Users/me'] });
    expect(custom.WISP_ALLOWED_WORKSPACE_ROOTS).toBe('/srv/only');
    expect(spawnEnvironment(input(launch)).WISP_ALLOWED_WORKSPACE_ROOTS).toBeUndefined();
  });

  it('turns on JSON logs only when asked', () => {
    expect(spawnEnvironment({ ...input({ python: 'p', root: '', kind: 'system' }), jsonLogs: true }).WISP_JSON_LOGS).toBe('1');
    expect(spawnEnvironment(input({ python: 'p', root: '', kind: 'system' })).WISP_JSON_LOGS).toBeUndefined();
  });
});

describe('OutputTail and describeStartupFailure', () => {
  it('keeps only the last lines and clips very long ones', () => {
    const tail = new OutputTail();
    tail.push(Array.from({ length: 100 }, (_, i) => `line ${i}`).join('\n'));
    tail.push('x'.repeat(2000));
    const text = tail.text(3);
    expect(text.split('\n')).toHaveLength(3);
    expect(text).toContain('line 99');
    expect(text).not.toContain('line 0\n');
    expect(text.split('\n').at(-1)!.length).toBeLessThan(450);
  });

  const launch: Launch = { python: BUNDLED, root: '', kind: 'bundled' };

  it('shows the backend\'s own last output when it died, so the cause is visible', () => {
    const tail = new OutputTail();
    tail.push("Traceback (most recent call last):\nModuleNotFoundError: No module named 'fastapi'\n");
    const msg = describeStartupFailure(launch, tail, { code: 1, signal: null }, null);
    expect(msg).toContain('bundled with this app');
    expect(msg).toContain('exited (code 1) before it was ready');
    expect(msg).toContain("No module named 'fastapi'");
  });

  it('names the signal when the process was killed', () => {
    expect(describeStartupFailure(launch, new OutputTail(), { code: null, signal: 'SIGKILL' }, null)).toContain('signal SIGKILL');
  });

  it('says a timeout is a timeout, not an exit', () => {
    expect(describeStartupFailure(launch, new OutputTail(), null, null)).toContain('did not become ready in time');
  });

  it('reports a spawn failure by itself', () => {
    expect(describeStartupFailure({ python: 'python3', root: '', kind: 'system' }, new OutputTail(), null, 'spawn python3 ENOENT'))
      .toBe("Could not start the system's python3 (python3): spawn python3 ENOENT");
  });

  it('explains the system-python fallback and how to fix it', () => {
    const msg = describeStartupFailure({ python: 'python3', root: '', kind: 'system' }, new OutputTail(), { code: 1, signal: null }, null);
    expect(msg).toContain('WISP_PYTHON');
  });

  it('does not print an empty "Last output" block', () => {
    expect(describeStartupFailure(launch, new OutputTail(), { code: 2, signal: null }, null)).not.toContain('Last output');
  });
});
