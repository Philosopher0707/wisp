import React from 'react';
import { ExternalLink } from '../../icons/index.js';
import './SearchResults.css';

interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

function parseResults(text: string): SearchResult[] {
  const results: SearchResult[] = [];
  const lines = text.split('\n');
  let current: Partial<SearchResult> = {};

  for (const line of lines) {
    const numMatch = line.match(/^(\d+)\.\s+(.+)/);
    if (numMatch) {
      if (current.title) {
        results.push({ title: current.title || '', url: current.url || '', snippet: current.snippet || '' });
      }
      current = { title: numMatch[2].trim(), url: '', snippet: '' };
      continue;
    }
    const urlMatch = line.match(/^\s*URL:\s+(.+)/);
    if (urlMatch) {
      current.url = urlMatch[1].trim();
      continue;
    }
    const snippet = line.trim();
    if (snippet && !snippet.startsWith('Web search results') && !snippet.startsWith('No results found')) {
      current.snippet = (current.snippet || '') + (current.snippet ? ' ' : '') + snippet;
    }
  }
  if (current.title) {
    results.push({ title: current.title || '', url: current.url || '', snippet: current.snippet || '' });
  }
  return results;
}

export interface SearchResultData {
  query: string;
  results: Array<{ number: number; title: string; url: string; snippet: string }>;
}

interface SearchResultsProps {
  text: string;
  structuredData?: SearchResultData;
}

export const SearchResults: React.FC<SearchResultsProps> = ({ text, structuredData }) => {
  let results: SearchResult[];
  if (structuredData?.results) {
    results = structuredData.results.map(r => ({
      title: r.title,
      url: r.url,
      snippet: r.snippet,
    }));
  } else {
    results = parseResults(text);
  }

  if (results.length === 0) {
    return <div className="sr-empty">{text}</div>;
  }

  return (
    <div className="sr-grid">
      {results.map((r, i) => (
        <a
          key={i}
          className="sr-card"
          href={r.url || '#'}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => { if (!r.url) e.preventDefault(); }}
        >
          <div className="sr-title">
            {r.title}
            {r.url && <ExternalLink size={10} className="sr-link-icon" />}
          </div>
          {r.url && <div className="sr-url">{r.url}</div>}
          {r.snippet && <div className="sr-snippet">{r.snippet.slice(0, 200)}</div>}
        </a>
      ))}
    </div>
  );
};
