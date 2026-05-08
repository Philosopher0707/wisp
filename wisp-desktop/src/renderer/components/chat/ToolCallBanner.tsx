import React from 'react';
import type { ToolCallItem } from '../../state/types.js';
import './ToolCallBanner.css';

interface Props {
  toolCall: ToolCallItem;
}

function argsPreview(args: Record<string, unknown>): string {
  const val = (args.path || args.command || args.content || '') as string;
  if (val) return String(val).slice(0, 60);
  return '...';
}

function resultPreview(result: string): string {
  return result.slice(0, 300).replace(/\n/g, ' ');
}

export const ToolCallBanner: React.FC<Props> = ({ toolCall }) => {
  const [expanded, setExpanded] = React.useState(false);

  return (
    <div className="tcb" onClick={() => toolCall.result && setExpanded(!expanded)}>
      <div className="tcb-row">
        <span className="tcb-icon">{toolCall.durationMs ? '✓' : '⏳'}</span>
        <span className="tcb-name">{toolCall.name}({argsPreview(toolCall.args)})</span>
        {toolCall.durationMs && (
          <span className="tcb-time">{toolCall.durationMs.toFixed(0)}ms</span>
        )}
      </div>
      {expanded && toolCall.result && (
        <div className="tcb-result">
          <code>{resultPreview(toolCall.result)}</code>
        </div>
      )}
    </div>
  );
};
