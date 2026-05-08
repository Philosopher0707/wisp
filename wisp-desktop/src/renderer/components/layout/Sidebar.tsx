import React, { useEffect } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi } from '../../hooks/useApi.js';
import { SidebarNav } from '../sidebar/SidebarNav.js';
import { PinnedSection } from '../sidebar/PinnedSection.js';
import { ProjectsSection } from '../sidebar/ProjectsSection.js';
import { SidebarFooter } from '../sidebar/SidebarFooter.js';
import './Sidebar.css';

export const Sidebar: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);

  useEffect(() => {
    if (state.connection === 'connected') {
      api.fetchSessions().then((sessions) => {
        dispatch({ type: 'SET_SESSIONS', sessions });
      }).catch(() => {});
    }
  }, [state.connection, state.sessionId]);

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <SidebarNav />
        <div className="sidebar-session-list">
          {state.sessions.slice(0, 10).map((s) => (
            <button
              key={s.id}
              className={`chat-list-item ${s.id === state.sessionId ? 'chat-list-item--active' : ''}`}
              onClick={() => dispatch({ type: 'SET_SESSION_ID', id: s.id })}
            >
              <span className="chat-list-item-title">{s.title || s.id.slice(0, 12)}</span>
              <span className="chat-list-item-time">{s.msg_count} msgs</span>
            </button>
          ))}
        </div>
        <PinnedSection />
        <ProjectsSection />
      </div>
      <SidebarFooter />
    </aside>
  );
};
