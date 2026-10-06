import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi } from '../../hooks/useApi.js';

interface Entry {
  id: number;
  command: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  error: string | null;
  ms: number;
}

let nextId = 1;

export const TerminalTab: React.FC = () => {
  const { state } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [sandbox, setSandbox] = useState<string | null>(null);
  const history = useRef<string[]>([]);
  const cursor = useRef(-1);
  const bottom = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' });
  }, [entries, running]);
  useEffect(() => field.current?.focus(), []);

  const run = useCallback(async () => {
    const command = input.trim();
    if (!command || running) return;
    history.current.push(command);
    cursor.current = -1;
    setInput('');
    setRunning(true);
    const started = performance.now();
    try {
      const res = await api.runCommand(command);
      if (res.sandbox) setSandbox(res.sandbox);
      setEntries((prev) => [...prev, { id: nextId++, command, stdout: res.stdout, stderr: res.stderr, exitCode: res.exit_code, error: null, ms: performance.now() - started }]);
    } catch (e) {
      setEntries((prev) => [...prev, { id: nextId++, command, stdout: '', stderr: '', exitCode: null, error: e instanceof Error ? e.message : 'Command failed to run.', ms: performance.now() - started }]);
    } finally {
      setRunning(false);
      field.current?.focus();
    }
  }, [api, input, running]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation();
    if (e.key === 'Enter') {
      void run();
    } else if (e.key === 'ArrowUp' && history.current.length > 0) {
      e.preventDefault();
      cursor.current = cursor.current < 0 ? history.current.length - 1 : Math.max(0, cursor.current - 1);
      setInput(history.current[cursor.current]);
    } else if (e.key === 'ArrowDown' && cursor.current >= 0) {
      e.preventDefault();
      cursor.current += 1;
      if (cursor.current >= history.current.length) {
        cursor.current = -1;
        setInput('');
      } else {
        setInput(history.current[cursor.current]);
      }
    } else if (e.key === 'l' && e.ctrlKey) {
      e.preventDefault();
      setEntries([]);
    }
  };

  const where = state.workspacePath ? state.workspacePath.split('/').pop() : 'workspace';

  return (
    <div className="dock-pane" onClick={() => field.current?.focus()}>
      <div className="dock-toolbar">
        <span className="dock-toolbar-title">Terminal</span>
        <span className="dock-toolbar-meta">{sandbox ? `${sandbox} sandbox` : 'workspace sandbox'}</span>
        <button className="dock-tool-btn dock-tool-btn--text" onClick={(e) => { e.stopPropagation(); setEntries([]); }} disabled={entries.length === 0}>
          Clear
        </button>
      </div>
      <div className="dock-scroll term-scroll">
        {entries.length === 0 && (
          <p className="dock-note">Run a command in <code>{where}</code>. Each command runs on its own through Wisp's sandbox and permission policy, so interactive programs (vim, a REPL) and a persistent shell are not available.</p>
        )}
        {entries.map((en) => (
          <div key={en.id} className="term-entry">
            <div className="term-cmd"><span className="term-prompt">$</span> {en.command}</div>
            {en.stdout && <pre className="term-out">{en.stdout}</pre>}
            {en.stderr && <pre className="term-out term-out--err">{en.stderr}</pre>}
            {en.error && <pre className="term-out term-out--err">{en.error}</pre>}
            {en.error && /approv|polic|forbidden/i.test(en.error) && (
              <p className="term-hint">Wisp's permission policy blocks direct shell commands in the current mode. Ask the agent to run it in the chat (you approve there), or change the permission mode yourself.</p>
            )}
            {!en.error && (
              <div className={`term-status${en.exitCode === 0 ? '' : ' term-status--fail'}`}>
                exit {en.exitCode} · {(en.ms / 1000).toFixed(1)}s
              </div>
            )}
          </div>
        ))}
        {running && <div className="term-running">Running…</div>}
        <div ref={bottom} />
      </div>
      <div className="term-input-row">
        <span className="term-prompt">$</span>
        <input
          ref={field}
          className="term-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={running ? 'Waiting for the command to finish…' : 'Type a command and press Enter'}
          disabled={running}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          aria-label="Terminal command"
        />
      </div>
    </div>
  );
};
