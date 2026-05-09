import React, { useState, useMemo, useCallback } from 'react';
import './DiffPreview.css';

interface HunkLine {
  prefix: string;
  content: string;
  lineNum?: number;
}

interface Hunk {
  header: string;
  lines: HunkLine[];
  oldStart: number;
  newStart: number;
}

interface DiffPreviewProps {
  diff: string;
  path: string;
  isNew: boolean;
  onApply?: (content: string) => void;
  onCancel?: () => void;
  compact?: boolean;
  saveLabel?: string;
}

function parseDiffHunks(diff: string): Hunk[] {
  if (!diff.trim()) return [];
  const rawLines = diff.split('\n');
  const hunks: Hunk[] = [];
  let current: HunkLine[] = [];
  let oldStart = 1;
  let newStart = 1;

  function flush() {
    if (current.length > 0) {
      const first = current[0];
      hunks.push({
        header: `@@ -${oldStart} +${newStart} @@`,
        lines: current,
        oldStart,
        newStart,
      });
    }
    current = [];
  }

  for (const raw of rawLines) {
    if (!raw || raw.trim() === '...') {
      flush();
      continue;
    }
    if (raw.length < 2) continue;

    const prefix = raw[0];
    const rest = raw.slice(1);
    const spaceIdx = rest.indexOf(' ');
    if (spaceIdx === -1) continue;

    const numStr = rest.slice(0, spaceIdx).trim();
    const content = rest.slice(spaceIdx + 1);
    const lineNum = numStr ? parseInt(numStr, 10) : undefined;

    if (prefix === '+' || prefix === '-') {
      if (current.length === 0) {
        oldStart = lineNum || oldStart;
        newStart = lineNum || newStart;
      }
      current.push({ prefix, content, lineNum });
    } else if (prefix === ' ') {
      current.push({ prefix, content, lineNum });
    }
  }
  flush();
  return hunks;
}

function reconstructContent(original: string, hunks: Hunk[], accepted: Set<number>): string {
  const originalLines = original.split('\n');
  const result: string[] = [...originalLines];

  // Apply accepted hunks in reverse order to preserve offsets
  const acceptedHunks = hunks.filter((_, i) => accepted.has(i));
  // Sort by position in original (descending for reverse application)
  const edits = acceptedHunks.map((h) => {
    const removedLines: number[] = [];
    const addedContent: string[] = [];
    let origLine = h.oldStart - 1; // 0-based

    for (const line of h.lines) {
      if (line.prefix === '-') {
        removedLines.push(origLine);
        origLine++;
      } else if (line.prefix === '+') {
        addedContent.push(line.content);
      } else {
        origLine++;
      }
    }

    return { removedLines, addedContent };
  });

  // Apply right-to-left
  for (let i = edits.length - 1; i >= 0; i--) {
    const { removedLines, addedContent } = edits[i];
    if (removedLines.length > 0) {
      const start = removedLines[0];
      const count = removedLines.length;
      result.splice(start, count, ...addedContent);
    } else {
      // Pure insertion
      const insertAt = hunks.filter((_, j) => accepted.has(j))[i].oldStart - 1;
      result.splice(insertAt, 0, ...addedContent);
    }
  }

  return result.join('\n');
}

export const DiffPreview: React.FC<DiffPreviewProps> = ({
  diff,
  path,
  isNew,
  onApply,
  onCancel,
  compact = false,
  saveLabel,
}) => {
  const hunks = useMemo(() => parseDiffHunks(diff), [diff]);
  const [acceptedHunks, setAcceptedHunks] = useState<Set<number>>(() => {
    // Default: all accepted
    return new Set(hunks.map((_, i) => i));
  });
  const [activeHunk, setActiveHunk] = useState(0);

  const toggleHunk = useCallback((idx: number) => {
    setAcceptedHunks((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }, []);

  const acceptAll = useCallback(() => {
    setAcceptedHunks(new Set(hunks.map((_, i) => i)));
  }, [hunks]);

  const rejectAll = useCallback(() => {
    setAcceptedHunks(new Set());
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.altKey && e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveHunk((prev) => Math.min(prev + 1, hunks.length - 1));
      } else if (e.altKey && e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveHunk((prev) => Math.max(prev - 1, 0));
      } else if (e.key === 'Enter' && !e.shiftKey && activeHunk < hunks.length) {
        e.preventDefault();
        toggleHunk(activeHunk);
      }
    },
    [hunks.length, activeHunk, toggleHunk],
  );

  const lineCount = hunks.reduce((sum, h) => sum + h.lines.length, 0);

  return (
    <div className="diff-preview" onKeyDown={handleKeyDown}>
      <div className="diff-header">
        <span className="diff-path">{path}</span>
        <span className={`diff-badge ${isNew ? 'diff-badge--new' : 'diff-badge--mod'}`}>
          {isNew ? 'new' : 'modified'}
        </span>
      </div>

      {hunks.length > 1 && (
        <div className="diff-hunk-toolbar">
          <button className="hunk-toolbar-btn" onClick={acceptAll}>Accept All</button>
          <button className="hunk-toolbar-btn" onClick={rejectAll}>Reject All</button>
          <span className="hunk-toolbar-info">
            {acceptedHunks.size}/{hunks.length} hunks
          </span>
        </div>
      )}

      <div className="diff-content">
        {hunks.map((hunk, hi) => {
          const isAccepted = acceptedHunks.has(hi);
          const isActive = hi === activeHunk;
          return (
            <div
              key={hi}
              className={`diff-hunk-group ${isAccepted ? '' : 'hunk-rejected'} ${isActive ? 'hunk-active' : ''}`}
            >
              <div className="diff-hunk-header" onClick={() => toggleHunk(hi)}>
                <button className={`hunk-toggle ${isAccepted ? 'hunk-toggle--on' : 'hunk-toggle--off'}`}>
                  {isAccepted ? '✓' : '✗'}
                </button>
                <span className="hunk-header-text">{hunk.header}</span>
                <span className="hunk-line-count">{hunk.lines.length} lines</span>
              </div>
              {hunk.lines.map((line, li) => {
                let cls = '';
                if (line.prefix === '+') cls = 'diff-add';
                else if (line.prefix === '-') cls = 'diff-rem';
                return (
                  <div key={li} className={`diff-line ${cls}`}>
                    <span className="diff-ln">{line.lineNum ?? ''}</span>
                    <span className="diff-text">{line.prefix}{line.content || ' '}</span>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {onApply && (
        <div className="diff-actions">
          <button className="diff-cancel" onClick={onCancel || onCancel}>Cancel</button>
          <button
            className="diff-confirm"
            onClick={() => {
              // For compact mode, just call onApply directly
              onApply('');
            }}
            disabled={acceptedHunks.size === 0}
            title={acceptedHunks.size === 0 ? 'Accept at least one hunk' : `Apply ${acceptedHunks.size} of ${hunks.length} hunks`}
          >
            {saveLabel || `Apply ${acceptedHunks.size}/${hunks.length} hunks`}
          </button>
        </div>
      )}

      {compact && (
        <div className="diff-compact-footer">
          {hunks.length} {hunks.length === 1 ? 'change' : 'changes'} · {lineCount} lines · {path}
        </div>
      )}
    </div>
  );
};
