import React, { useState } from 'react';
import type { Message } from '../../state/types.js';
import { useAppState } from '../../state/context.js';
import { renderMarkdown } from '../../utils/markdown.js';
import { ToolCallBanner } from './ToolCallBanner.js';
import { Copy, RefreshCw, CornerDownLeft, GitBranch } from '../../icons/index.js';
import { relativeTime, formatTokens } from '../../utils/time.js';
import '../../utils/markdown.css';
import './MessageBubble.css';

interface Props {
  msg: Message;
  highlighted?: boolean;
}

export const MessageBubble: React.FC<Props> = ({ msg, highlighted }) => {
  const { state, dispatch, sendMessage } = useAppState();
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [showTools, setShowTools] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(msg.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleContinue = () => {
    dispatch({ type: 'SUBMIT_MESSAGE', content: 'Continue.' });
    sendMessage({
      type: 'prompt',
      content: 'Continue.',
      model: state.selectedModel,
      session_id: state.sessionId || undefined,
      show_thinking: state.showThinking,
      system_prompt: state.systemPrompt || undefined,
      temperature: parseFloat(localStorage.getItem('wisp_temperature') || '0.7'),
      permission_mode: state.permissionMode,
    });
  };

  const handleRegenerate = () => {
    const idx = state.messages.indexOf(msg);
    const prevMsg = idx > 0 ? state.messages[idx - 1] : null;
    if (prevMsg && prevMsg.role === 'user') {
      dispatch({ type: 'SUBMIT_MESSAGE', content: prevMsg.content });
      sendMessage({
        type: 'prompt',
        content: prevMsg.content,
        model: state.selectedModel,
        session_id: state.sessionId || undefined,
        show_thinking: state.showThinking,
        system_prompt: state.systemPrompt || undefined,
        temperature: parseFloat(localStorage.getItem('wisp_temperature') || '0.7'),
        permission_mode: state.permissionMode,
      });
    }
  };

  const startEdit = () => {
    setEditValue(msg.content);
    setEditing(true);
  };

  const submitEdit = () => {
    const trimmed = editValue.trim();
    if (!trimmed) return;
    setEditing(false);
    dispatch({ type: 'SUBMIT_MESSAGE', content: trimmed });
    sendMessage({
      type: 'prompt',
      content: trimmed,
      model: state.selectedModel,
      session_id: state.sessionId || undefined,
      show_thinking: state.showThinking,
      system_prompt: state.systemPrompt || undefined,
      temperature: parseFloat(localStorage.getItem('wisp_temperature') || '0.7'),
      permission_mode: state.permissionMode,
    });
  };

  const cancelEdit = () => setEditing(false);

  const handleFork = async () => {
    const idx = state.messages.indexOf(msg);
    if (idx < 0) return;
    // Fork BEFORE this assistant message — keep only prior messages
    const priorMsgs = state.messages.slice(0, idx);
    const rawMessages: Record<string, unknown>[] = [];
    for (const m of priorMsgs) {
      if (m.role === 'user') {
        rawMessages.push({ role: 'user', content: m.content });
      } else if (m.role === 'assistant') {
        const raw: Record<string, unknown> = { role: 'assistant', content: m.content };
        if (m.thinking) raw.thinking = m.thinking;
        if (m.toolCalls) {
          raw.tool_calls = m.toolCalls.map((tc) => ({
            function: { name: tc.name, arguments: tc.args },
          }));
        }
        rawMessages.push(raw);
        if (m.toolCalls) {
          for (let i = 0; i < m.toolCalls.length; i++) {
            const tc = m.toolCalls[i];
            if (tc.result !== undefined) {
              rawMessages.push({
                role: 'tool',
                content: tc.result,
                name: tc.name,
                tool_call_id: `fc-${i}`,
              });
            }
          }
        }
      }
    }
    dispatch({ type: 'FORK_SESSION', messageId: msg.id });
    try {
      const base = state.serverUrl.replace(/\/$/, '');
      const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';
      const resp = await fetch(`${base}/api/sessions/fork${params}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: rawMessages, title: msg.content?.slice(0, 40) || 'Fork' }),
      });
      if (resp.ok) {
        const data = await resp.json() as { session_id: string };
        dispatch({ type: 'FORK_SESSION_DONE', sessionId: data.session_id });
        dispatch({ type: 'SET_SESSION_ID', id: data.session_id });
      } else {
        dispatch({ type: 'CANCEL_FORK' });
      }
    } catch {
      dispatch({ type: 'CANCEL_FORK' });
    }
  };

  if (msg.role === 'user') {
    return (
      <div className={`msg-row msg-row--user${highlighted ? ' msg-row--highlighted' : ''}`}>
        <div className="msg-bubble msg-bubble--user">
          {editing ? (
            <div className="msg-edit-box">
              <textarea
                className="msg-edit-input"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    submitEdit();
                  }
                  if (e.key === 'Escape') cancelEdit();
                }}
                autoFocus
                rows={3}
              />
              <div className="msg-edit-actions">
                <span className="msg-edit-hint">Enter to submit &middot; Esc to cancel</span>
                <div className="msg-edit-btns">
                  <button className="msg-edit-cancel" onClick={cancelEdit}>Cancel</button>
                  <button className="msg-edit-submit" onClick={submitEdit}>
                    <CornerDownLeft size={12} />
                    <span>Resend</span>
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="msg-content">{renderMarkdown(msg.content)}</div>
              <div className="msg-actions msg-actions--user">
                <button className="msg-action-btn" title="Edit" onClick={startEdit}>
                  <span>Edit</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  if (msg.role === 'system') {
    return (
      <div className="msg-row msg-row--system">
        <p className="msg-system">{msg.content}</p>
      </div>
    );
  }

  const isLastAssistant =
    state.messages.length > 0 &&
    state.messages[state.messages.length - 1].id === msg.id;

  return (
    <div className={`msg-row msg-row--assistant${highlighted ? ' msg-row--highlighted' : ''}`}>
      <div className="msg-bubble msg-bubble--assistant">
        {msg.thinking && (
          <details className="msg-thinking-details">
            <summary className="msg-thinking-toggle">Thinking...</summary>
            <p className="msg-thinking">{msg.thinking.slice(-500)}</p>
          </details>
        )}
        {msg.content && <div className="msg-content">{renderMarkdown(msg.content)}</div>}
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="msg-tools-summary">
            <button
              className="msg-tools-toggle"
              onClick={() => setShowTools(!showTools)}
              title={showTools ? 'Hide tool details' : 'Show tool details'}
            >
              <span className="msg-tools-arrow">{showTools ? '▾' : '▸'}</span>
              <span className="msg-tools-label">
                {msg.toolCalls.length} tool{msg.toolCalls.length !== 1 ? 's' : ''} used
              </span>
              <span className="msg-tools-names">
                {msg.toolCalls.map((tc) => tc.name).join(', ')}
              </span>
            </button>
            {showTools && (
              <div className="msg-tools-list">
                {msg.toolCalls.map((tc, i) => (
                  <ToolCallBanner key={i} toolCall={tc} />
                ))}
              </div>
            )}
          </div>
        )}
        {!msg.content && !msg.thinking && (!msg.toolCalls || msg.toolCalls.length === 0) && (
          <p className="msg-loading">Thinking...</p>
        )}
        {msg.content && !state.isStreaming && (
          <div className="msg-actions">
            <button className="msg-action-btn" title="Copy" onClick={handleCopy}>
              <Copy size={14} />
              {copied && <span className="msg-action-label">Copied!</span>}
            </button>
            <button className="msg-action-btn" title="Fork from here" onClick={handleFork}>
              <GitBranch size={14} />
            </button>
            <button className="msg-action-btn" title="Regenerate" onClick={handleRegenerate}>
              <RefreshCw size={14} />
            </button>
            {isLastAssistant && (
              <button className="msg-action-btn" title="Continue generation" onClick={handleContinue}>
                <span>Continue</span>
              </button>
            )}
            <span className="msg-meta">
              {msg.timestamp && relativeTime(msg.timestamp)}
              {msg.tokens != null && <>&nbsp;&middot;&nbsp;~{formatTokens(msg.tokens)}</>}
            </span>
          </div>
        )}
      </div>
    </div>
  );
};
