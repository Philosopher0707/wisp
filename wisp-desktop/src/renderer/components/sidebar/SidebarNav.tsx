import React from 'react';
import { Pencil, Search, Grid3x3, Bot } from '../../icons/index.js';
import './SidebarNav.css';

interface NavItem {
  icon: React.FC<{ size?: number }>;
  label: string;
}

const navItems: NavItem[] = [
  { icon: Pencil, label: 'New Chat' },
  { icon: Search, label: 'Search' },
  { icon: Grid3x3, label: 'Plugins' },
  { icon: Bot, label: 'Automations' },
];

export const SidebarNav: React.FC = () => {
  return (
    <nav className="sidebar-nav">
      {navItems.map((item) => (
        <button key={item.label} className="sidebar-nav-item">
          <item.icon size={16} />
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
};
