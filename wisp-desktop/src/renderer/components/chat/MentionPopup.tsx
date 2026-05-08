import React, { useEffect, useState, useCallback, useRef } from 'react';
import { File } from '../../icons/index.js';
import './MentionPopup.css';

interface FileMatch {
  name: string;
  path: string;
}

interface Props {
  query: string;
  workspacePath: string;
  serverUrl: string;
  apiKey: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

export const MentionPopup: React.FC<Props> = ({
  query, workspacePath, serverUrl, apiKey, onSelect, onClose,
}) => {
  const [results, setResults] = useState<FileMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const searchFiles = useCallback(async (q: string) => {
    if (!q && !workspacePath) {
      setResults([]);
      return;
    }
    setLoading(true);
    try {
      const base = serverUrl.replace(/\/$/, '');
      const p = workspacePath || '';
      const params = new URLSearchParams();
      if (apiKey) params.set('api-key', apiKey);
      params.set('path', p);
      const resp = await fetch(`${base}/api/files?${params.toString()}`);
      const data = await resp.json() as { items?: { name: string; path: string; type: string }[] };
      const items = (data.items || [])
        .filter((item) => {
          if (!q) return true;
          return item.name.toLowerCase().includes(q.toLowerCase());
        })
        .slice(0, 15)
        .map((item) => ({ name: item.name, path: item.path }));
      setResults(items);
      setSelectedIdx(0);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [serverUrl, apiKey, workspacePath]);

  useEffect(() => {
    searchFiles(query);
  }, [query, searchFiles]);

  // Scroll selected into view
  useEffect(() => {
    const el = listRef.current?.children[selectedIdx] as HTMLElement | undefined;
    el?.scrollIntoView({ block: 'nearest' });
  }, [selectedIdx]);

  const handleKeyDown = (e: KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setSelectedIdx((i) => Math.min(i + 1, results.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setSelectedIdx((i) => Math.max(i - 1, 0));
        break;
      case 'Enter':
        e.preventDefault();
        if (results[selectedIdx]) {
          onSelect(results[selectedIdx].path);
        }
        break;
      case 'Escape':
        e.preventDefault();
        onClose();
        break;
    }
  };

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  });

  if (!loading && results.length === 0) return null;

  return (
    <div className="mention-popup">
      <div className="mention-header">Files in workspace</div>
      <div className="mention-list" ref={listRef}>
        {loading ? (
          <div className="mention-empty">Searching...</div>
        ) : results.length === 0 ? (
          <div className="mention-empty">No files matched</div>
        ) : (
          results.map((f, i) => (
            <button
              key={f.path}
              className={`mention-item ${i === selectedIdx ? 'mention-item--selected' : ''}`}
              onClick={() => onSelect(f.path)}
              onMouseEnter={() => setSelectedIdx(i)}
            >
              <File size={13} className="mention-item-icon" />
              <span className="mention-item-name">{f.name}</span>
              <span className="mention-item-path">{f.path}</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
};
