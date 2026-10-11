import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi, type GitDiff, type GitDiffFile } from '../../hooks/useApi.js';
import { RefreshCw, ChevronDown, ChevronRight } from '../../icons/index.js';
import { parseUnifiedDiff } from '../../utils/diff.js';

const AUTO_EXPAND = 3;

const FileDiff: React.FC<{ file: GitDiffFile; open: boolean; onToggle: () => void }> = ({ file, open, onToggle }) => {
  const parsed = useMemo(() => parseUnifiedDiff(file.diff), [file.diff]);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <section className="diff-file">
      <button className="diff-file-head" onClick={onToggle} aria-expanded={open}>
        <Chevron size={13} />
        <span className="diff-file-path" title={file.path}>{file.path}</span>
        <span className={`diff-badge diff-badge--${file.status}`}>{file.status}</span>
        <span className="diff-count diff-count--add">+{parsed.added}</span>
        <span className="diff-count diff-count--del">−{parsed.removed}</span>
      </button>
      {open && (
        <div className="diff-body" role="table" aria-label={`Changes in ${file.path}`}>
          {file.binary ? (
            <p className="dock-note">Binary file: no text diff.</p>
          ) : parsed.lines.length === 0 ? (
            <p className="dock-note">No textual changes (mode or empty file).</p>
          ) : (
            parsed.lines.filter((l) => l.kind !== 'meta' || l.text.startsWith('\\')).map((l, i) => (
              <div key={i} className={`diff-line diff-line--${l.kind}`} role="row">
                <span className="diff-no">{l.oldNo ?? ''}</span>
                <span className="diff-no">{l.newNo ?? ''}</span>
                <span className="diff-sign">{l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : ''}</span>
                <span className="diff-text">{l.text}</span>
              </div>
            ))
          )}
          {file.clipped && <p className="dock-note">Diff clipped at 60 KB. Open the file to see the rest.</p>}
        </div>
      )}
    </section>
  );
};

export const DiffTab: React.FC = () => {
  const { state } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [data, setData] = useState<GitDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const requestId = useRef(0);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const next = await api.fetchGitDiff();
      if (id !== requestId.current) return;
      setData(next);
      setError(null);
    } catch (e) {
      if (id === requestId.current) setError(e instanceof Error ? e.message : 'Could not read changes.');
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [api]);

  // Refresh when the dock opens, when the workspace changes, and every time the agent finishes a turn.
  useEffect(() => {
    if (state.connection !== 'connected' || state.isStreaming) return;
    void load();
  }, [load, state.connection, state.isStreaming, state.workspacePath]);

  const totals = useMemo(() => {
    let added = 0;
    let removed = 0;
    for (const f of data?.files ?? []) {
      const p = parseUnifiedDiff(f.diff);
      added += p.added;
      removed += p.removed;
    }
    return { added, removed };
  }, [data]);

  const isOpen = (path: string, index: number) => (index < AUTO_EXPAND ? !closed.has(path) : opened.has(path));
  const toggle = (path: string, index: number) => {
    const setter = index < AUTO_EXPAND ? setClosed : setOpened;
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <div className="dock-pane">
      <div className="dock-toolbar">
        <span className="dock-toolbar-title">
          {data?.git ? `${data.files.length} changed file${data.files.length === 1 ? '' : 's'}` : 'Changes'}
        </span>
        {data?.git && data.files.length > 0 && (
          <span className="dock-toolbar-meta">
            <span className="diff-count diff-count--add">+{totals.added}</span>
            <span className="diff-count diff-count--del">−{totals.removed}</span>
          </span>
        )}
        <button className="dock-tool-btn" onClick={() => void load()} title="Refresh" aria-label="Refresh changes" disabled={loading}>
          <RefreshCw size={14} className={loading ? 'spin' : undefined} />
        </button>
      </div>
      <div className="dock-scroll">
        {error && <p className="dock-note dock-note--error">{error}</p>}
        {!error && data && !data.git && <p className="dock-note">This workspace is not a git repository, so there is nothing to compare against.</p>}
        {!error && data?.git && data.files.length === 0 && <p className="dock-note">No uncommitted changes.</p>}
        {data?.files.map((f, i) => (
          <FileDiff key={f.path} file={f} open={isOpen(f.path, i)} onToggle={() => toggle(f.path, i)} />
        ))}
        {data?.truncated && <p className="dock-note">Showing the first {data.files.length} files.</p>}
      </div>
    </div>
  );
};
