import React from 'react';
import { useAppState } from '../../state/context.js';
import { Settings } from '../../icons/index.js';
import { PillButton } from '../common/PillButton.js';
import './SidebarFooter.css';

export const SidebarFooter: React.FC = () => {
  const { state, dispatch } = useAppState();
  const collapsed = state.sidebarCollapsed;

  return (
    <div className={`sidebar-footer${collapsed ? ' sidebar-footer--collapsed' : ''}`}>
      <button
        className="sidebar-footer-settings"
        title="Settings"
        onClick={() => dispatch({ type: 'OPEN_OVERLAY', overlay: 'settings' })}
      >
        <Settings size={16} />
      </button>
      {!collapsed && <PillButton variant="outlined">Upgrade</PillButton>}
    </div>
  );
};
