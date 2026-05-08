import React from 'react';
import './IconButton.css';

interface IconButtonProps {
  icon: React.FC<{ size?: number }>;
  size?: number;
  title?: string;
}

export const IconButton: React.FC<IconButtonProps> = ({ icon: Icon, size = 18, title }) => {
  return (
    <button className="icon-btn" title={title} aria-label={title}>
      <Icon size={size} />
    </button>
  );
};
