import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useAppState } from '../state/context.js';
import { File, X, CornerDownLeft } from '../icons/index.js';
import './QuickFileModal.css';

interface FileEntry {
  name: string;
  path: string;
  size: number;
}

function fuzzyMatch(query: string, path: string): number {
  const q = query.toLowerCase();
  const p = path.toLowerCase();
  let qi = 0;
  let score = 0;
  let consecutive = 0;
  for (let i = 0; i < p.length && qi < q.length; i++) {
    if (p[i] === q[qi]) {
      qi++;
      consecutive++;
      score += consecutive * 2 + (p[i] === '/' ? 5 : 0);
    } else {
      consecutive = 0;
    }
  }
  return qi === q.length ? score : 0;
}

export const QuickFileModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [query, setQuery] = useState('');
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const base = state.serverUrl.replace(/\/$/, '');

    fetch(`${base}/api/files/tree`, {
      headers: state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : undefined,
    })
      .then((r) => r.json())
      .then((data: { files?: FileEntry[] }) => {
        setFiles(data.files || []);
        setLoading(false);
      })
      .catch(() => {
        setFiles([]);
        setLoading(false);
      });
  }, [state.serverUrl, state.apiKey]);

  const results = useMemo(() => {
    if (!query.trim()) return files.slice(0, 50);
    const scored = files
      .map((f) => ({ entry: f, score: fuzzyMatch(query, f.path) }))
      .filter((s) => s.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 30);
    return scored.map((s) => s.entry);
  }, [files, query]);

  useEffect(() => {
    setSelectedIdx(0);
  }, [query]);

  const close = useCallback(() => {
    dispatch({ type: 'CLOSE_OVERLAY' });
  }, [dispatch]);

  const selectFile = useCallback((entry: FileEntry) => {
    dispatch({ type: 'TOGGLE_RIGHT_PANEL' });
    dispatch({ type: 'SELECT_FILE', path: entry.path });
    close();
  }, [dispatch, close]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIdx((prev) => Math.min(prev + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIdx((prev) => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (results[selectedIdx]) {
        selectFile(results[selectedIdx]);
      }
    }
  };

  // Scroll selected into view
  useEffect(() => {
    if (listRef.current) {
      const item = listRef.current.children[selectedIdx] as HTMLElement | undefined;
      if (item) {
        item.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [selectedIdx]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <div className="overlay" onClick={close}>
      <div className="qfm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="qfm-input-row">
          <span className="qfm-icon">/</span>
          <input
            ref={inputRef}
            className="qfm-input"
            type="text"
            placeholder="Search files by name..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button className="qfm-close" onClick={close} title="Close (Esc)">
            <X size={14} />
          </button>
        </div>
        <div className="qfm-results" ref={listRef}>
          {loading && <p className="qfm-empty">Indexing files...</p>}
          {!loading && results.length === 0 && (
            <p className="qfm-empty">
              {files.length === 0
                ? 'No files found in workspace'
                : 'No matching files'}
            </p>
          )}
          {results.map((entry, idx) => (
            <button
              key={entry.path}
              className={`qfm-item ${idx === selectedIdx ? 'qfm-item--selected' : ''}`}
              onClick={() => selectFile(entry)}
              onMouseEnter={() => setSelectedIdx(idx)}
            >
              <File size={15} className="qfm-item-icon" />
              <span className="qfm-item-name">{entry.name}</span>
              <span className="qfm-item-path">
                {entry.path !== entry.name ? entry.path : ''}
              </span>
            </button>
          ))}
        </div>
        <div className="qfm-footer">
          <span className="qfm-hint">
            <CornerDownLeft size={12} /> to open &middot; Esc to close
          </span>
          <span className="qfm-count">{files.length} files indexed</span>
        </div>
      </div>
    </div>
  );
};
