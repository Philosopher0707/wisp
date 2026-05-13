import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useAppState } from '../../state/context.js';
import { WelcomeHeading } from './WelcomeHeading.js';
import { ChatInput } from './ChatInput.js';
import { MessageBubble } from './MessageBubble.js';
import { ProjectContextBar } from './ProjectContextBar.js';
import { ConversationSearch } from './ConversationSearch.js';
import { GitCommitBanner } from './GitCommitBanner.js';
import { BackgroundAgentBanner } from './BackgroundAgentBanner.js';
import { SubagentPanel } from '../SubagentPanel.js';
import { ArrowDown } from '../../icons/index.js';
import './ChatArea.css';

export const ChatArea: React.FC = () => {
  const { state, dispatch } = useAppState();
  const scrollRef = useRef<HTMLDivElement>(null);
  const msgRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const hasMessages = state.messages.length > 0;
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const [copyToast, setCopyToast] = useState(false);

  const handleCommit = async () => {
    setCommitting(true);
    try {
      const base = state.serverUrl.replace(/\/$/, '');
      const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';
      const resp = await fetch(`${base}/api/git/commit${params}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : {}),
        },
        body: JSON.stringify({}),
      });
      if (resp.ok) {
        dispatch({ type: 'DISMISS_GIT_BANNER' });
      }
    } catch { /* ignore */ }
    setCommitting(false);
  };

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      setIsAtBottom(true);
    }
  }, []);

  // Track scroll position
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const threshold = 60;
    setIsAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < threshold);
  }, []);

  // Reset scroll to bottom when session changes
  useEffect(() => {
    scrollToBottom();
  }, [state.sessionId, scrollToBottom]);

  // Auto-scroll on new messages only if already at bottom
  useEffect(() => {
    if (isAtBottom) {
      scrollToBottom();
    }
  }, [state.messages, isAtBottom, scrollToBottom]);

  // Scroll to highlighted message
  const handleHighlight = useCallback((msgId: string) => {
    setHighlightId(msgId);
    const el = msgRefs.current.get(msgId);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

  const closeSearch = useCallback(() => {
    dispatch({ type: 'TOGGLE_CONV_SEARCH' });
    setHighlightId(null);
  }, [dispatch]);

  const handleMouseUp = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    const text = sel.toString().trim();
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      setCopyToast(true);
      setTimeout(() => setCopyToast(false), 1200);
    }).catch(() => {});
  }, []);

  if (state.isLoadingSession) {
    return (
      <div className="chat-area">
        <div className="chat-area-center">
          <div className="chat-loading">
            <div className="chat-loading-spinner" />
            <p>Loading session...</p>
          </div>
        </div>
      </div>
    );
  }

  if (hasMessages) {
    return (
      <div className="chat-area chat-area--conversation">
        {state.convSearchActive && (
          <ConversationSearch
            messages={state.messages}
            onClose={closeSearch}
            onHighlight={handleHighlight}
          />
        )}
        <div className="chat-messages" ref={scrollRef} onScroll={handleScroll} onMouseUp={handleMouseUp}>
          {state.messages.map((msg) => (
            <div
              key={msg.id}
              ref={(el) => { if (el) msgRefs.current.set(msg.id, el); }}
            >
              <MessageBubble msg={msg} highlighted={msg.id === highlightId} />
            </div>
          ))}
        </div>
        {!isAtBottom && (
          <button className="chat-scroll-btn" onClick={scrollToBottom} title="Scroll to bottom">
            <ArrowDown size={16} />
          </button>
        )}
        {copyToast && <div className="chat-copy-toast">Copied</div>}
        {state.gitCommitBanner && (
          <GitCommitBanner
            branch={state.gitCommitBanner.branch}
            changedFiles={state.gitCommitBanner.changedFiles}
            onCommit={handleCommit}
            onDismiss={() => dispatch({ type: 'DISMISS_GIT_BANNER' })}
            committing={committing}
          />
        )}
        <BackgroundAgentBanner />
        {state.subagentTasks.length > 0 && <SubagentPanel />}
        <div className="chat-input-sticky">
          <div className="chat-input-centered">
            <ChatInput />
            <ProjectContextBar />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-area">
      <div className="chat-area-center">
        <WelcomeHeading />
        {state.subagentTasks.length > 0 && <SubagentPanel />}
        <ChatInput />
        <ProjectContextBar />
      </div>
    </div>
  );
};
