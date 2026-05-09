import React from 'react';
import { useAppState } from '../../state/context.js';
import './ContextIndicator.css';

const MAX_CONTEXT_TOKENS = 200000; // Claude default

function estimateTokens(messages: { content?: string; thinking?: string }[]): number {
  let total = 0;
  for (const msg of messages) {
    const chars = (msg.content?.length || 0) + (msg.thinking?.length || 0);
    total += Math.ceil(chars / 4);
  }
  return total;
}

function getUsageLevel(pct: number): 'low' | 'medium' | 'high' | 'critical' {
  if (pct < 50) return 'low';
  if (pct < 75) return 'medium';
  if (pct < 90) return 'high';
  return 'critical';
}

export const ContextIndicator: React.FC = () => {
  const { state } = useAppState();

  const estimatedTokens = estimateTokens(state.messages);
  const percent = Math.min(100, Math.round((estimatedTokens / MAX_CONTEXT_TOKENS) * 100));
  const level = getUsageLevel(percent);

  if (state.messages.length === 0) return null;

  return (
    <div className="context-indicator" title={`~${estimatedTokens.toLocaleString()} tokens used (${percent}% of ${MAX_CONTEXT_TOKENS.toLocaleString()})`}>
      <div className="context-indicator-track">
        <div
          className={`context-indicator-fill context-indicator-fill--${level}`}
          style={{ width: `${Math.max(2, percent)}%` }}
        />
      </div>
      <span className={`context-indicator-label context-indicator-label--${level}`}>
        {percent}%
      </span>
    </div>
  );
};
