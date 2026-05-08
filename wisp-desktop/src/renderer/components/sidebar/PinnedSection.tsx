import React from 'react';
import { ChatListItem } from './ChatListItem.js';
import './SidebarSections.css';

export const PinnedSection: React.FC = () => {
  return (
    <div className="sidebar-section">
      <div className="sidebar-section-header">
        <span>Pinned</span>
      </div>
      <div className="sidebar-section-items">
        <ChatListItem />
        <ChatListItem />
      </div>
    </div>
  );
};
