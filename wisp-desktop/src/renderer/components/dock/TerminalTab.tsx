import React, { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { RefreshCw } from '../../icons/index.js';

const THEME = {
  background: '#0d0d0d',
  foreground: '#e6e6e6',
  cursor: '#c4b5fd',
  selectionBackground: 'rgba(139, 92, 246, 0.35)',
};

/**
 * A real shell on the host (see src/main/terminal.ts). The shell lives in the main process, so switching tabs or closing
 * the dock does not kill it: reopening redraws its recent output and carries on.
 */
export const TerminalTab: React.FC = () => {
  const host = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<{ cwd?: string; shell?: string; exited?: string; error?: string }>({});
  const [generation, setGeneration] = useState(0);
  const api = window.wisp?.terminal;

  useEffect(() => {
    const el = host.current;
    if (!api || !el) return undefined;

    const term = new Terminal({
      fontFamily: 'SF Mono, Menlo, monospace',
      fontSize: 12,
      cursorBlink: true,
      scrollback: 5000,
      theme: THEME,
      allowProposedApi: false,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    fit.fit();

    let disposed = false;
    const offData = api.onData((d) => term.write(d));
    const offExit = api.onExit((info) => {
      setStatus((s) => ({ ...s, exited: info.error ?? `Shell exited (${info.signal ?? `code ${info.code}`}).` }));
    });
    const input = term.onData((d) => api.write(d));
    const resized = term.onResize(({ cols, rows }) => api.resize(cols, rows));

    void api.start(term.cols, term.rows).then((res) => {
      if (disposed) return;
      if (!res.ok) {
        setStatus({ error: res.error ?? 'Could not start a shell.' });
        return;
      }
      if (res.buffer) term.write(res.buffer);
      setStatus({ cwd: res.cwd, shell: res.shell });
      term.focus();
    });

    const observer = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // the pane is momentarily zero-sized while the dock resizes
      }
    });
    observer.observe(el);

    return () => {
      disposed = true;
      observer.disconnect();
      input.dispose();
      resized.dispose();
      offData();
      offExit();
      term.dispose();
    };
  }, [api, generation]);

  if (!api) {
    return <div className="dock-pane"><p className="dock-note">The terminal needs the desktop app; it is not available in a plain web page.</p></div>;
  }

  const restart = () => {
    api.restart();
    setStatus({});
    setGeneration((g) => g + 1);
  };

  return (
    <div className="dock-pane">
      <div className="dock-toolbar">
        <span className="dock-toolbar-title">Terminal</span>
        <span className="dock-toolbar-meta" title={status.cwd}>{status.cwd ? `${status.shell?.split('/').pop()} · ${status.cwd.split('/').pop()}` : 'host shell'}</span>
        <button className="dock-tool-btn" onClick={restart} title="Restart shell" aria-label="Restart shell"><RefreshCw size={14} /></button>
      </div>
      {(status.exited || status.error) && (
        <p className="dock-note dock-note--error">{status.error ?? status.exited} <button className="term-link" onClick={restart}>Start a new shell</button></p>
      )}
      <div className="term-host" ref={host} />
    </div>
  );
};
