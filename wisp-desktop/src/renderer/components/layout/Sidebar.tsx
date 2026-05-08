import React, { useEffect, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi } from '../../hooks/useApi.js';
import { Trash2 } from '../../icons/index.js';
import { SidebarNav } from '../sidebar/SidebarNav.js';
import { PinnedSection } from '../sidebar/PinnedSection.js';
import { ProjectsSection } from '../sidebar/ProjectsSection.js';
import { SidebarFooter } from '../sidebar/SidebarFooter.js';
import './Sidebar.css';

export const Sidebar: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [loadingId, setLoadingId] = useState<string | null>(null);

  useEffect(() => {
    if (state.connection === 'connected') {
      api.fetchSessions().then((sessions) => {
        dispatch({ type: 'SET_SESSIONS', sessions });
      }).catch(() => {});
    }
  }, [state.connection, state.sessionsVersion]);

  const handleSelectSession = async (id: string) => {
    if (id === state.sessionId) return;
    setLoadingId(id);

    try {
      const messages = await api.fetchSession(id);
      dispatch({ type: 'SET_SESSION_ID', id });
      dispatch({ type: 'SET_MESSAGES', messages });
    } catch {
      dispatch({ type: 'RECEIVE_ERROR', message: 'Failed to load session' });
    } finally {
      setLoadingId(null);
    }
  };

  const handleDeleteSession = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    const ok = await api.deleteSession(id);
    if (ok) {
      if (id === state.sessionId) {
        dispatch({ type: 'NEW_CHAT' });
      }
      // trigger session list refresh
      dispatch({ type: 'RECEIVE_COMPLETE' });
    }
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <SidebarNav />
        <div className="sidebar-session-list">
          {state.sessions.slice(0, 15).map((s) => (
            <button
              key={s.id}
              className={`chat-list-item ${s.id === state.sessionId ? 'chat-list-item--active' : ''}`}
              onClick={() => handleSelectSession(s.id)}
            >
              <span className="chat-list-item-title">
                {loadingId === s.id ? 'Loading...' : (s.title || s.id.slice(0, 12))}
              </span>
              <span className="chat-list-item-time">{s.msg_count} msgs</span>
              <button
                className="chat-list-delete-btn"
                title="Delete session"
                onClick={(e) => handleDeleteSession(e, s.id)}
              >
                <Trash2 size={13} />
              </button>
            </button>
          ))}
          {state.sessions.length === 0 && (
            <p className="sidebar-empty">No sessions yet</p>
          )}
        </div>
        <PinnedSection />
        <ProjectsSection />
      </div>
      <SidebarFooter />
    </aside>
  );
};
