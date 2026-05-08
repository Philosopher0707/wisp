import React, { useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi } from '../../hooks/useApi.js';
import { ChatListItem } from './ChatListItem.js';
import './SidebarSections.css';

export const PinnedSection: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const pinned = state.sessions.filter((s) => state.pinnedSessionIds.has(s.id));

  if (pinned.length === 0) return null;

  const handleSelect = async (id: string) => {
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

  const handleTogglePin = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    dispatch({ type: 'TOGGLE_PIN_SESSION', sessionId: id });
  };

  return (
    <div className="sidebar-section">
      <div className="sidebar-section-header">
        <span>Pinned</span>
      </div>
      <div className="sidebar-section-items">
        {pinned.map((s) => (
          <ChatListItem
            key={s.id}
            id={s.id}
            title={loadingId === s.id ? 'Loading...' : (s.title || s.id.slice(0, 12))}
            subtitle={`${s.msg_count} msgs`}
            pinned
            active={s.id === state.sessionId}
            onClick={() => handleSelect(s.id)}
            onTogglePin={handleTogglePin}
          />
        ))}
      </div>
    </div>
  );
};
