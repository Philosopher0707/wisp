import React from 'react';
import { Pin, ExternalLink } from '../../icons/index.js';
import './SidebarSections.css';

export const ChatListItem: React.FC = () => {
  return (
    <button className="chat-list-item">
      <Pin size={11} className="chat-list-item-pin" />
      <span className="chat-list-item-title">Project setup &amp; config</span>
      <span className="chat-list-item-time">2h ago</span>
      <ExternalLink size={11} className="chat-list-item-ext" />
    </button>
  );
};
