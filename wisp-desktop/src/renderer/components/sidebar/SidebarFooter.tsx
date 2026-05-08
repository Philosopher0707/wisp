import React from 'react';
import { useAppState } from '../../state/context.js';
import { Settings } from '../../icons/index.js';
import { PillButton } from '../common/PillButton.js';
import './SidebarFooter.css';

export const SidebarFooter: React.FC = () => {
  const { dispatch } = useAppState();

  return (
    <div className="sidebar-footer">
      <button
        className="sidebar-footer-settings"
        title="Settings"
        onClick={() => dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' })}
      >
        <Settings size={16} />
      </button>
      <PillButton variant="outlined">Upgrade</PillButton>
    </div>
  );
};
