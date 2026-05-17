import React, { useState, useCallback } from 'react';
import { useAppState } from '../state/context.js';
import './ArenaPanel.css';

interface SideData {
  side: string;
  summary: string;
  diff: string;
  files_changed: string[];
  duration_ms: number;
}

interface ArenaResult {
  entry_id: string;
  task: string;
  side_a: SideData;
  side_b: SideData;
  voted: boolean;
}

interface RevealedData {
  entry_id: string;
  model_a: string;
  model_b: string;
  vote: string;
  revealed: boolean;
}

export const ArenaPanel: React.FC = () => {
  const { state } = useAppState();
  const [prompt, setPrompt] = useState('');
  const [task, setTask] = useState('');
  const [modelA, setModelA] = useState('claude-sonnet-4-6');
  const [modelB, setModelB] = useState('claude-opus-4-7');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ArenaResult | null>(null);
  const [revealed, setRevealed] = useState<RevealedData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeSides, setActiveSides] = useState<Record<string, 'diff' | 'summary'>>({ a: 'summary', b: 'summary' });

  const startComparison = useCallback(async () => {
    if (!prompt.trim()) return;
    setRunning(true);
    setError(null);
    setResult(null);
    setRevealed(null);

    try {
      const base = state.serverUrl.replace(/\/$/, '');

      const resp = await fetch(`${base}/api/arena/compare`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : {}),
        },
        body: JSON.stringify({
          prompt: prompt.trim(),
          task: task.trim() || prompt.trim().slice(0, 100),
          model_a: modelA,
          model_b: modelB,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      setResult(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setRunning(false);
    }
  }, [prompt, task, modelA, modelB, state.serverUrl, state.apiKey]);

  const submitVote = useCallback(async (vote: string) => {
    if (!result) return;
    try {
      const base = state.serverUrl.replace(/\/$/, '');

      const resp = await fetch(`${base}/api/arena/vote`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : {}),
        },
        body: JSON.stringify({ entry_id: result.entry_id, vote }),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setRevealed(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, [result, state.serverUrl, state.apiKey]);

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  const renderSide = (side: SideData, isLeft: boolean) => {
    const tab = activeSides[side.side.toLowerCase()];
    const diffLines = side.diff ? side.diff.split('\n') : [];
    return (
      <div className="arena-side">
        <div className="arena-side-header">
          <span className="arena-side-label">Model {side.side}</span>
          <span className="arena-side-duration">{formatDuration(side.duration_ms)}</span>
        </div>
        <div className="arena-side-tabs">
          <button
            className={`arena-tab ${tab === 'summary' ? 'arena-tab--active' : ''}`}
            onClick={() => setActiveSides((prev) => ({ ...prev, [side.side.toLowerCase()]: 'summary' }))}
          >
            Summary
          </button>
          <button
            className={`arena-tab ${tab === 'diff' ? 'arena-tab--active' : ''}`}
            onClick={() => setActiveSides((prev) => ({ ...prev, [side.side.toLowerCase()]: 'diff' }))}
          >
            Diff {side.diff ? `(${diffLines.length}L)` : ''}
          </button>
        </div>
        <div className="arena-side-body">
          {tab === 'summary' ? (
            <div className="arena-summary">{side.summary || 'No output'}</div>
          ) : (
            <div className="arena-diff">
              {diffLines.length > 0 ? (
                diffLines.slice(0, 200).map((line, i) => {
                  let cls = '';
                  if (line.startsWith('+')) cls = 'arena-diff-add';
                  else if (line.startsWith('-')) cls = 'arena-diff-rem';
                  else if (line.startsWith('@@')) cls = 'arena-diff-hunk';
                  return (
                    <div key={i} className={`arena-diff-line ${cls}`}>
                      {line || ' '}
                    </div>
                  );
                })
              ) : (
                <div className="arena-no-diff">No diff available</div>
              )}
            </div>
          )}
        </div>
        {side.files_changed.length > 0 && (
          <div className="arena-side-files">
            {side.files_changed.length} file(s): {side.files_changed.slice(0, 5).join(', ')}
            {side.files_changed.length > 5 ? '...' : ''}
          </div>
        )}
      </div>
    );
  };

  if (revealed) {
    return (
      <div className="arena-panel">
        <div className="arena-header">
          <span className="arena-title">Arena — Results</span>
          <button className="arena-close" onClick={() => { setResult(null); setRevealed(null); }}>
            New Comparison
          </button>
        </div>
        <div className="arena-revealed">
          <div className={`arena-model-card ${revealed.vote === 'a' ? 'arena-model-card--winner' : ''}`}>
            <div className="arena-model-name">{revealed.model_a}</div>
            <div className="arena-model-side">Model A</div>
            {revealed.vote === 'a' && <div className="arena-winner-badge">Your Pick</div>}
          </div>
          <div className="arena-vs">vs</div>
          <div className={`arena-model-card ${revealed.vote === 'b' ? 'arena-model-card--winner' : ''}`}>
            <div className="arena-model-name">{revealed.model_b}</div>
            <div className="arena-model-side">Model B</div>
            {revealed.vote === 'b' && <div className="arena-winner-badge">Your Pick</div>}
          </div>
          {revealed.vote === 'tie' && <div className="arena-tie-badge">Tie</div>}
        </div>
      </div>
    );
  }

  if (result) {
    return (
      <div className="arena-panel">
        <div className="arena-header">
          <span className="arena-title">Arena — Blind Comparison</span>
        </div>
        <div className="arena-task">Task: {result.task}</div>
        <div className="arena-comparison">
          {renderSide(result.side_a, true)}
          <div className="arena-divider" />
          {renderSide(result.side_b, false)}
        </div>
        <div className="arena-vote-bar">
          <span className="arena-vote-prompt">Which result is better?</span>
          <div className="arena-vote-buttons">
            <button className="arena-vote-btn arena-vote-btn--a" onClick={() => submitVote('a')}>
              Model A
            </button>
            <button className="arena-vote-btn arena-vote-btn--tie" onClick={() => submitVote('tie')}>
              Tie
            </button>
            <button className="arena-vote-btn arena-vote-btn--b" onClick={() => submitVote('b')}>
              Model B
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="arena-panel">
      <div className="arena-header">
        <span className="arena-title">Arena — Compare Models</span>
      </div>
      <div className="arena-form">
        <div className="arena-field">
          <label className="arena-label">Task Description</label>
          <input
            className="arena-input"
            placeholder="e.g., Add input validation to the login form"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            disabled={running}
          />
        </div>
        <div className="arena-field">
          <label className="arena-label">Prompt</label>
          <textarea
            className="arena-textarea"
            placeholder="Full prompt for both models..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={4}
            disabled={running}
          />
        </div>
        <div className="arena-model-row">
          <div className="arena-field">
            <label className="arena-label">Model A</label>
            <select className="arena-select" value={modelA} onChange={(e) => setModelA(e.target.value)} disabled={running}>
              {state.availableModels.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
              {state.availableModels.length === 0 && (
                <>
                  <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
                  <option value="claude-opus-4-7">claude-opus-4-7</option>
                  <option value="claude-haiku-4-5">claude-haiku-4-5</option>
                </>
              )}
            </select>
          </div>
          <span className="arena-vs-label">vs</span>
          <div className="arena-field">
            <label className="arena-label">Model B</label>
            <select className="arena-select" value={modelB} onChange={(e) => setModelB(e.target.value)} disabled={running}>
              {state.availableModels.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
              {state.availableModels.length === 0 && (
                <>
                  <option value="claude-opus-4-7">claude-opus-4-7</option>
                  <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
                  <option value="claude-haiku-4-5">claude-haiku-4-5</option>
                </>
              )}
            </select>
          </div>
        </div>
        {error && <div className="arena-error">{error}</div>}
        <button
          className="arena-run-btn"
          onClick={startComparison}
          disabled={running || !prompt.trim()}
        >
          {running ? (
            <>
              <span className="arena-spinner" />
              Running comparison...
            </>
          ) : 'Compare Models'}
        </button>
      </div>
    </div>
  );
};
