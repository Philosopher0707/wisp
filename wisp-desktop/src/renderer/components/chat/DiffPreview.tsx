import React from 'react';
import './DiffPreview.css';

interface DiffPreviewProps {
  diff: string;
  path: string;
  isNew: boolean;
}

function highlightDiffLine(line: string): { cls: string; text: string } {
  if (line.startsWith('+')) return { cls: 'diff-add', text: line };
  if (line.startsWith('-')) return { cls: 'diff-rem', text: line };
  if (line.startsWith('@@')) return { cls: 'diff-hunk', text: line };
  return { cls: '', text: line };
}

export const DiffPreview: React.FC<DiffPreviewProps> = ({ diff, path, isNew }) => {
  const lines = diff.split('\n');

  return (
    <div className="diff-preview">
      <div className="diff-header">
        <span className="diff-path">{path}</span>
        <span className={`diff-badge ${isNew ? 'diff-badge--new' : 'diff-badge--mod'}`}>
          {isNew ? 'new' : 'modified'}
        </span>
      </div>
      <div className="diff-content">
        {lines.map((line, i) => {
          const { cls, text } = highlightDiffLine(line);
          return (
            <div key={i} className={`diff-line ${cls}`}>
              <span className="diff-ln">{i + 1}</span>
              <span className="diff-text">{text || ' '}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
