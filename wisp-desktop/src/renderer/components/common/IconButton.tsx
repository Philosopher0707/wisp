import React from 'react';
import './IconButton.css';

interface IconButtonProps {
  icon: React.FC<{ size?: number }>;
  size?: number;
  title?: string;
  active?: boolean;
  onClick?: () => void;
}

export const IconButton: React.FC<IconButtonProps> = ({ icon: Icon, size = 18, title, active, onClick }) => {
  return (
    <button
      className={`icon-btn${active ? ' icon-btn--active' : ''}`}
      title={title}
      aria-label={title}
      onClick={onClick}
    >
      <Icon size={size} />
    </button>
  );
};
