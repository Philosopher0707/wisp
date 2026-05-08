import React, { useEffect, useState, useMemo } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi } from '../../hooks/useApi.js';
import { Trash2, Pin, ChevronLeft, ChevronRight, Search, X, Square, CheckSquare } from '../../icons/index.js';
import { SidebarNav } from '../sidebar/SidebarNav.js';
import { PinnedSection } from '../sidebar/PinnedSection.js';
import { ProjectsSection } from '../sidebar/ProjectsSection.js';
import { SidebarFooter } from '../sidebar/SidebarFooter.js';
import './Sidebar.css';

export const Sidebar: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [filterText, setFilterText] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);

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
      dispatch({ type: 'RECEIVE_COMPLETE' });
    }
  };

  const startRename = (e: React.MouseEvent, id: string, title: string) => {
    e.stopPropagation();
    setEditingId(id);
    setEditTitle(title);
  };

  const commitRename = async () => {
    const id = editingId;
    const title = editTitle.trim();
    setEditingId(null);
    setEditTitle('');
    if (!id || !title) return;
    await api.renameSession(id, title);
    dispatch({ type: 'RECEIVE_COMPLETE' });
  };

  const cancelRename = () => {
    setEditingId(null);
    setEditTitle('');
  };

  const toggleSelect = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedIds(next);
  };

  const selectAll = () => {
    setSelectedIds(new Set(filteredSessions.map((s) => s.id)));
  };

  const deselectAll = () => {
    setSelectedIds(new Set());
  };

  const deleteSelected = async () => {
    if (selectedIds.size === 0) return;
    setDeleting(true);
    const results = await Promise.allSettled(
      [...selectedIds].map((id) => api.deleteSession(id)),
    );
    setDeleting(false);
    setSelectedIds(new Set());
    if (selectedIds.has(state.sessionId || '')) {
      dispatch({ type: 'NEW_CHAT' });
    }
    dispatch({ type: 'RECEIVE_COMPLETE' });
  };

  const collapsed = state.sidebarCollapsed;

  const filteredSessions = useMemo(() => {
    if (!filterText) return state.sessions.slice(0, 15);
    const q = filterText.toLowerCase();
    return state.sessions.filter(
      (s) => (s.title || s.id).toLowerCase().includes(q),
    ).slice(0, 15);
  }, [state.sessions, filterText]);

  return (
    <aside className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`}>
      <div className="sidebar-top">
        <SidebarNav />
        {!collapsed && (
          <>
            <div className="sidebar-session-filter">
              <Search size={12} className="sidebar-filter-icon" />
              <input
                className="sidebar-filter-input"
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                placeholder="Filter sessions..."
                onKeyDown={(e) => e.stopPropagation()}
              />
              {filterText && (
                <button
                  className="sidebar-filter-clear"
                  onClick={() => setFilterText('')}
                >
                  ×
                </button>
              )}
            </div>
            {selectedIds.size > 0 && (
              <div className="sidebar-bulk-bar">
                <span className="sidebar-bulk-count">{selectedIds.size} selected</span>
                <button className="sidebar-bulk-btn" onClick={selectAll}>All</button>
                <button className="sidebar-bulk-btn" onClick={deselectAll}>None</button>
                <button
                  className="sidebar-bulk-delete"
                  onClick={deleteSelected}
                  disabled={deleting}
                >
                  <Trash2 size={13} />
                  <span>{deleting ? 'Deleting...' : 'Delete'}</span>
                </button>
              </div>
            )}
            <div className="sidebar-session-list">
              {filteredSessions.map((s) => (
                <div
                  key={s.id}
                  className={`chat-list-item ${s.id === state.sessionId ? 'chat-list-item--active' : ''}`}
                  onClick={() => handleSelectSession(s.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleSelectSession(s.id); }}
                >
                  <span
                    className="chat-list-item-check"
                    onClick={(e) => toggleSelect(e, s.id)}
                    title={selectedIds.has(s.id) ? 'Deselect' : 'Select'}
                  >
                    {selectedIds.has(s.id)
                      ? <CheckSquare size={13} />
                      : <Square size={13} />
                    }
                  </span>
                  <span
                    className={`chat-list-item-pin${
                      state.pinnedSessionIds.has(s.id) ? ' chat-list-item-pin--pinned' : ''
                    }`}
                    title={state.pinnedSessionIds.has(s.id) ? 'Unpin' : 'Pin'}
                    onClick={(e) => {
                      e.stopPropagation();
                      dispatch({ type: 'TOGGLE_PIN_SESSION', sessionId: s.id });
                    }}
                  >
                    <Pin size={11} />
                  </span>
                  <span className="chat-list-item-title">
                    {loadingId === s.id ? 'Loading...' : editingId === s.id ? (
                      <input
                        className="chat-list-rename-input"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onBlur={commitRename}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') commitRename();
                          if (e.key === 'Escape') cancelRename();
                        }}
                        onClick={(e) => e.stopPropagation()}
                        autoFocus
                      />
                    ) : (
                      <span
                        className="chat-list-title-text"
                        onDoubleClick={(e) => startRename(e, s.id, s.title || s.id.slice(0, 12))}
                        title="Double-click to rename"
                      >
                        {s.title || s.id.slice(0, 12)}
                      </span>
                    )}
                  </span>
                  <span className="chat-list-item-time">{s.msg_count} msgs</span>
                  <button
                    className="chat-list-delete-btn"
                    title="Delete session"
                    onClick={(e) => handleDeleteSession(e, s.id)}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
              {filteredSessions.length === 0 && (
                <p className="sidebar-empty">
                  {filterText ? 'No matching sessions' : 'No sessions yet'}
                </p>
              )}
            </div>
          </>
        )}
        {!collapsed && <PinnedSection />}
        {!collapsed && <ProjectsSection />}
      </div>
      <div className="sidebar-bottom-group">
        <button
          className="sidebar-collapse-btn"
          onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
        <SidebarFooter />
      </div>
    </aside>
  );
};
