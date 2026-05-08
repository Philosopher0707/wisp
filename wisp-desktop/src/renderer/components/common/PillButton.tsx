import React from 'react';
import './PillButton.css';

interface PillButtonProps {
  children: React.ReactNode;
  variant: 'filled' | 'outlined';
  icon?: React.FC<{ size?: number }>;
  color?: 'purple';
}

export const PillButton: React.FC<PillButtonProps> = ({ children, variant, icon: Icon, color }) => {
  const cls = ['pill-btn', `pill-btn--${variant}`, color ? `pill-btn--${color}` : ''].filter(Boolean).join(' ');
  return (
    <button className={cls}>
      {Icon && <Icon size={14} />}
      <span>{children}</span>
    </button>
  );
};
