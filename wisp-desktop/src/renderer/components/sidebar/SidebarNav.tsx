import React from 'react';
import { useAppState } from '../../state/context.js';
import { Pencil, Search, Grid3x3, Bot } from '../../icons/index.js';
import './SidebarNav.css';

export const SidebarNav: React.FC = () => {
  const { dispatch } = useAppState();

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
      case 'Automations':
        dispatch({ type: 'OPEN_OVERLAY', overlay: 'plugins' });
        break;
    }
  };

  const navItems = [
    { icon: Pencil, label: 'New Chat' },
    { icon: Search, label: 'Search' },
    { icon: Grid3x3, label: 'Plugins' },
    { icon: Bot, label: 'Automations' },
  ];

  return (
    <nav className="sidebar-nav">
      {navItems.map((item) => (
        <button key={item.label} className="sidebar-nav-item" onClick={() => handleClick(item.label)}>
          <item.icon size={16} />
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
};
