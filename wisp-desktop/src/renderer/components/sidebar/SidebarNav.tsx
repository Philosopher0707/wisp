import React from 'react';
import { useAppState } from '../../state/context.js';
import { Pencil, Search, Grid3x3 } from '../../icons/index.js';
import './SidebarNav.css';

export const SidebarNav: React.FC = () => {
  const { state, dispatch } = useAppState();
  const collapsed = state.sidebarCollapsed;

  const handleClick = (label: string) => {
    switch (label) {
      case 'New Chat':
        dispatch({ type: 'NEW_CHAT' });
        break;
      case 'Search':
        dispatch({ type: 'OPEN_OVERLAY', overlay: 'search' });
        break;
      case 'Plugins':
        dispatch({ type: 'OPEN_OVERLAY', overlay: 'plugins' });
        break;
    }
  };

  const navItems = [
    { icon: Pencil, label: 'New Chat' },
    { icon: Search, label: 'Search' },
    { icon: Grid3x3, label: 'Plugins' },
  ];

  return (
    <nav className={`sidebar-nav${collapsed ? ' sidebar-nav--collapsed' : ''}`}>
      {navItems.map((item) => (
        <button
          key={item.label}
          className="sidebar-nav-item"
          onClick={() => handleClick(item.label)}
          title={collapsed ? item.label : undefined}
        >
          <item.icon size={16} />
          {!collapsed && <span>{item.label}</span>}
        </button>
      ))}
    </nav>
  );
};
