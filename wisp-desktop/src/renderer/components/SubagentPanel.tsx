import React, { useEffect, useRef, useCallback, useState } from 'react';
import { useAppState } from '../state/context.js';
import { CheckSquare, X } from '../icons/index.js';
import type { SubagentTask } from '../state/types.js';
import './SubagentPanel.css';

const AUTO_DISMISS_MS = 10_000;

function formatDuration(ms: number | null): string {
  if (ms == null) return '';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const min = Math.floor(ms / 60_000);
  const sec = Math.floor((ms % 60_000) / 1000);
  return `${min}m ${sec}s`;
}

function useElapsed(startTime: number | null): string {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!startTime) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [startTime]);
  if (!startTime) return '';
  return formatDuration(now - startTime);
}

export const SubagentPanel: React.FC = () => {
  const { state, dispatch } = useAppState();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [taskStartTimes, setTaskStartTimes] = useState<Map<string, number>>(new Map());

  // Record start times for running tasks
  useEffect(() => {
    const next = new Map(taskStartTimes);
    let changed = false;
    for (const task of state.subagentTasks) {
      if (task.status === 'running' && !next.has(task.id)) {
        next.set(task.id, Date.now());
        changed = true;
      }
    }
    if (changed) setTaskStartTimes(next);
  }, [state.subagentTasks]);

  // Auto-dismiss completed tasks
  useEffect(() => {
    const now = Date.now();
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (const task of state.subagentTasks) {
      if (task.status === 'done') {
        const completedAt = taskStartTimes.get(task.id);
        if (completedAt) {
          const elapsed = now - completedAt;
          if (elapsed > AUTO_DISMISS_MS) {
            timers.push(setTimeout(() => {
              dispatch({ type: 'CLEAR_SUBAGENT_TASKS' });
            }, 0));
          } else {
            timers.push(setTimeout(() => {
              dispatch({ type: 'CLEAR_SUBAGENT_TASKS' });
            }, AUTO_DISMISS_MS - elapsed));
          }
        }
      }
    }
    return () => timers.forEach(clearTimeout);
  }, [state.subagentTasks, taskStartTimes, dispatch]);

  // Auto-scroll to bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.subagentTasks]);

  const hasFinishedTasks = state.subagentTasks.some((t) => t.status === 'done' || t.status === 'failed');

  return (
    <div className="subagent-panel" ref={scrollRef}>
      <div className="subagent-header">
        <span className="subagent-header-title">
          Subagents ({state.subagentTasks.length})
        </span>
        {hasFinishedTasks && (
          <button
            className="subagent-clear-btn"
            onClick={() => dispatch({ type: 'CLEAR_SUBAGENT_TASKS' })}
          >
            Clear finished
          </button>
        )}
      </div>
      <div className="subagent-list">
        {state.subagentTasks.map((task) => (
          <SubagentTaskItem key={task.id} task={task} startTime={taskStartTimes.get(task.id) || null} />
        ))}
      </div>
    </div>
  );
};

/**
 * Renders one child's authority surface as THREE distinct claims.
 *
 * The temptation is a single line reading "52 tools" or "restricted", but the
 * three states below are genuinely different facts and collapsing any pair of
 * them misleads the user about what the agent may do:
 *
 * - `unbounded: true`      — inherits the full unrestricted surface.
 * - `capabilities: [...]`  — a real, narrowed list. Shorter means LESS authority.
 * - `capabilities: null`   — the backend reported nothing. Unknown, not "none".
 *
 * Note there is deliberately no `unbounded ? caps.length : 0` shortcut: a
 * narrowed child and an unreported child are different, and only the explicit
 * boolean can tell them apart.
 */
const CapabilitySummary: React.FC<{ task: SubagentTask }> = ({ task }) => {
  if (task.unbounded) {
    return (
      <span className="subagent-caps subagent-caps-unbounded" title="Inherits the full unrestricted tool surface">
        unrestricted
      </span>
    );
  }
  if (task.capabilities === null) {
    return (
      <span className="subagent-caps subagent-caps-unknown" title="Authority not reported by the backend">
        authority unreported
      </span>
    );
  }
  return (
    <span
      className="subagent-caps subagent-caps-narrowed"
      title={task.capabilities.join(', ')}
    >
      {task.capabilities.length} tool{task.capabilities.length === 1 ? '' : 's'}
    </span>
  );
};

const SubagentTaskItem: React.FC<{ task: SubagentTask; startTime: number | null }> = ({ task, startTime }) => {
  const elapsed = useElapsed(startTime);
  const isRunning = task.status === 'running';

  return (
    <div className={`subagent-task subagent-task--${task.status}`}>
      {isRunning ? (
        <div className="subagent-spinner" />
      ) : (
        <div className="subagent-task-icon">
          {task.status === 'done' ? <CheckSquare size={14} /> : <X size={14} />}
        </div>
      )}
      <div className="subagent-task-info">
        <div className="subagent-task-name">
          {task.name}
          <CapabilitySummary task={task} />
        </div>
        <div className="subagent-task-meta">
          {task.status === 'running' && (
            <span className="subagent-task-progress">
              {task.description}
            </span>
          )}
          {task.status === 'failed' && task.error && (
            <span className="subagent-task-error">{task.error}</span>
          )}
          {task.status === 'done' && (
            <span className="subagent-task-progress">{task.description}</span>
          )}
          {isRunning && elapsed && (
            <span className="subagent-task-duration">{elapsed}</span>
          )}
          {task.status === 'done' && task.durationMs != null && (
            <span className="subagent-task-duration">{formatDuration(task.durationMs)}</span>
          )}
          {task.status === 'done' && task.filesChanged.length > 0 && (
            <span className="subagent-task-files">
              {task.filesChanged.length} file{task.filesChanged.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
