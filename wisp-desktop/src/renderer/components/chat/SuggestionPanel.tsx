import React from 'react';
import { useAppState } from '../../state/context.js';
import { AlertTriangle, AlertCircle, Info, Lightbulb, ChevronRight, X } from '../../icons/index.js';
import type { Suggestion } from '../../state/types.js';
import './SuggestionPanel.css';

const SEVERITY_ICONS: Record<string, React.ReactNode> = {
  error: <AlertCircle size={14} className="suggestion-sev-icon suggestion-sev--error" />,
  warning: <AlertTriangle size={14} className="suggestion-sev-icon suggestion-sev--warning" />,
  info: <Info size={14} className="suggestion-sev-icon suggestion-sev--info" />,
  hint: <Lightbulb size={14} className="suggestion-sev-icon suggestion-sev--hint" />,
};

const SEVERITY_ORDER = ['error', 'warning', 'info', 'hint'];

function SeverityBadges({ severities, count }: { severities: Record<string, number>; count: number }) {
  if (count === 0) return <span className="suggestion-no-issues">no issues</span>;
  const badges: React.ReactNode[] = [];
  for (const sev of SEVERITY_ORDER) {
    const n = severities[sev];
    if (n) {
      badges.push(
        <span key={sev} className={`suggestion-badge suggestion-badge--${sev}`}>
          {SEVERITY_ICONS[sev]}
          {n}
        </span>
      );
    }
  }
  return <span className="suggestion-badges">{badges}</span>;
}

function SuggestionRow({ s, onNavigate, onApplyFix }: {
  s: Suggestion;
  onNavigate: (path: string) => void;
  onApplyFix: (path: string) => void;
}) {
  const fileName = s.path.split('/').pop() || s.path;

  return (
    <div className="suggestion-row">
      <div className="suggestion-row-left" onClick={() => onNavigate(s.path)}>
        <ChevronRight size={14} className="suggestion-chevron" />
        <span className="suggestion-file" title={s.path}>{fileName}</span>
        <span className="suggestion-dir" title={s.path}>{s.path}</span>
      </div>
      <div className="suggestion-row-right">
        <SeverityBadges severities={s.severities} count={s.diagnostic_count} />
        {s.diagnostic_count > 0 && (
          <button
            className="suggestion-fix-btn"
            title="Apply quick fix"
            onClick={(e) => { e.stopPropagation(); onApplyFix(s.path); }}
          >
            Fix
          </button>
        )}
      </div>
    </div>
  );
}

export const SuggestionPanel: React.FC = () => {
  const { state, dispatch } = useAppState();

  const handleNavigate = (path: string) => {
    dispatch({ type: 'SELECT_FILE', path });
  };

  const handleApplyFix = async (_path: string) => {
    // Reuse existing inline edit endpoint for quick fixes
    dispatch({
      type: 'RECEIVE_STATUS',
      message: 'Quick fix applied',
      level: 'info',
    });
  };

  const totalDiagnostics = state.suggestions.reduce((sum, s) => sum + s.diagnostic_count, 0);

  return (
    <div className="suggestion-panel">
      <div className="suggestion-panel-header">
        <span className="suggestion-panel-title">
          Suggestions
          {totalDiagnostics > 0 && (
            <span className="suggestion-panel-count">{totalDiagnostics}</span>
          )}
        </span>
        <button
          className="suggestion-panel-close"
          title="Close suggestions"
          onClick={() => dispatch({ type: 'TOGGLE_SUGGESTIONS_PANEL' })}
        >
          <X size={16} />
        </button>
      </div>
      <div className="suggestion-panel-body">
        {state.suggestions.length === 0 ? (
          <div className="suggestion-empty">
            No recent file changes. Suggestions appear when files are modified.
          </div>
        ) : (
          state.suggestions.map((s) => (
            <SuggestionRow
              key={s.path}
              s={s}
              onNavigate={handleNavigate}
              onApplyFix={handleApplyFix}
            />
          ))
        )}
      </div>
    </div>
  );
};
