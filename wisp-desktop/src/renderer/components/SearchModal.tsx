import React, { useRef, useState, useEffect } from 'react';
import { useAppState } from '../state/context.js';
import { Search } from '../icons/index.js';
import './SearchModal.css';

export const SearchModal: React.FC = () => {
  const { state, dispatch } = useAppState();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<string[]>([]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSearch = (value: string) => {
    setQuery(value);
    if (value.length < 2) {
      setResults([]);
      return;
    }
    // Search in session titles
    const matches = state.sessions
      .filter((s) => s.title && s.title.toLowerCase().includes(value.toLowerCase()))
      .map((s) => s.title || s.id);
    setResults(matches.slice(0, 10));
  };

  const close = () => dispatch({ type: 'CLOSE_OVERLAY' });

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
            placeholder="Search sessions, commands, settings..."
            onKeyDown={(e) => {
              if (e.key === 'Escape') close();
            }}
          />
        </div>
        {results.length > 0 && (
          <div className="search-results">
            {results.map((r, i) => (
              <button key={i} className="search-result-item">
                {r}
              </button>
            ))}
          </div>
        )}
        {query.length >= 2 && results.length === 0 && (
          <p className="search-empty">No results found</p>
        )}
      </div>
    </div>
  );
};
