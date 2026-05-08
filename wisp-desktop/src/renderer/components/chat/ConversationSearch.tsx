import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Search, X, ChevronUp, ChevronDown } from '../../icons/index.js';
import './ConversationSearch.css';

interface Match {
  msgIdx: number;
  text: string;
}

interface Props {
  messages: { id: string; role: string; content: string }[];
  onClose: () => void;
  onHighlight: (msgId: string, matchIdx: number) => void;
}

export const ConversationSearch: React.FC<Props> = ({ messages, onClose, onHighlight }) => {
  const [query, setQuery] = useState('');
  const [matchIdx, setMatchIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const matchesRef = useRef<Match[]>([]);

  const search = useCallback((q: string) => {
    const results: Match[] = [];
    if (q.length >= 2) {
      const lower = q.toLowerCase();
      for (let i = 0; i < messages.length; i++) {
        const content = messages[i].content;
        if (content && content.toLowerCase().includes(lower)) {
          results.push({ msgIdx: i, text: content.slice(0, 80) });
        }
      }
    }
    matchesRef.current = results;
    setMatchIdx(0);
    if (results.length > 0) {
      onHighlight(messages[results[0].msgIdx].id, 0);
    }
  }, [messages, onHighlight]);

  const handleChange = (value: string) => {
    setQuery(value);
    search(value);
  };

  useEffect(() => { inputRef.current?.focus(); }, []);

  const navigate = (dir: 1 | -1) => {
    const matches = matchesRef.current;
    if (matches.length === 0) return;
    const next = (matchIdx + dir + matches.length) % matches.length;
    setMatchIdx(next);
    onHighlight(messages[matches[next].msgIdx].id, next);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { onClose(); return; }
    if (e.key === 'Enter') {
      e.preventDefault();
      navigate(e.shiftKey ? -1 : 1);
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); navigate(1); }
    if (e.key === 'ArrowUp') { e.preventDefault(); navigate(-1); }
  };

  const total = matchesRef.current.length;

  return (
    <div className="conv-search">
      <Search size={14} className="conv-search-icon" />
      <input
        ref={inputRef}
        className="conv-search-input"
        value={query}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Search conversation..."
      />
      {total > 0 && (
        <span className="conv-search-count">{matchIdx + 1}/{total}</span>
      )}
      {total > 0 && (
        <>
          <button className="conv-search-nav" onClick={() => navigate(-1)} title="Previous">
            <ChevronUp size={14} />
          </button>
          <button className="conv-search-nav" onClick={() => navigate(1)} title="Next">
            <ChevronDown size={14} />
          </button>
        </>
      )}
      <button className="conv-search-close" onClick={onClose} title="Close">
        <X size={14} />
      </button>
    </div>
  );
};
