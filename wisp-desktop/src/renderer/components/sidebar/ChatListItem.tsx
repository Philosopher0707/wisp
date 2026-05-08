import React from 'react';
import { Pin } from '../../icons/index.js';
import './SidebarSections.css';

interface Props {
  id: string;
  title: string;
  subtitle: string;
  pinned: boolean;
  active?: boolean;
  onClick: () => void;
  onTogglePin: (e: React.MouseEvent, id: string) => void;
}

export const ChatListItem: React.FC<Props> = ({
  id, title, subtitle, pinned, active, onClick, onTogglePin,
}) => {
  return (
    <button
      className={`chat-list-item${active ? ' chat-list-item--active' : ''}`}
      onClick={onClick}
    >
      <span
        className={`chat-list-item-pin${pinned ? ' chat-list-item-pin--pinned' : ''}`}
        title={pinned ? 'Unpin' : 'Pin'}
        onClick={(e) => onTogglePin(e, id)}
      >
        <Pin size={11} />
      </span>
      <span className="chat-list-item-title">{title}</span>
      <span className="chat-list-item-time">{subtitle}</span>
    </button>
  );
};
