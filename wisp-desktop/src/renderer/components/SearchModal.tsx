import React, { useRef, useState, useEffect, useCallback } from 'react';
import { useAppState } from '../state/context.js';
import { useApi } from '../hooks/useApi.js';
import { Search } from '../icons/index.js';
import type { SessionSummary } from '../state/types.js';
import './SearchModal.css';

export const SearchModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SessionSummary[]>([]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSearch = (value: string) => {
    setQuery(value);
    if (value.length < 2) {
      setResults([]);
      return;
    }
    const matches = state.sessions.filter(
      (s) => s.title?.toLowerCase().includes(value.toLowerCase()),
    );
    setResults(matches.slice(0, 10));
  };

  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

  const handleSelect = useCallback(async (session: SessionSummary) => {
    close();
    try {
      const messages = await api.fetchSession(session.id);
      dispatch({ type: 'SET_SESSION_ID', id: session.id });
      dispatch({ type: 'SET_MESSAGES', messages });
    } catch {
      dispatch({ type: 'RECEIVE_ERROR', message: 'Failed to load session' });
    }
  }, [api, dispatch]);

  return (
    <div className="overlay" onClick={close}>
      <div className="search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="search-input-row">
          <Search size={16} />
          <input
            ref={inputRef}
            className="search-field"
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder="Search sessions..."
            onKeyDown={(e) => {
              if (e.key === 'Escape') close();
              if (e.key === 'Enter' && results.length > 0) {
                handleSelect(results[0]);
              }
            }}
          />
        </div>
        {results.length > 0 && (
          <div className="search-results">
            {results.map((s) => (
              <button
                key={s.id}
                className="search-result-item"
                onClick={() => handleSelect(s)}
              >
                <span className="search-result-title">{s.title || s.id.slice(0, 12)}</span>
                <span className="search-result-meta">
                  {s.msg_count} msgs &middot; {s.model?.slice(0, 20)}
                </span>
              </button>
            ))}
          </div>
        )}
        {query.length >= 2 && results.length === 0 && (
          <p className="search-empty">No sessions found</p>
        )}
      </div>
    </div>
  );
};
