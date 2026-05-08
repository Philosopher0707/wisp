import React from 'react';
import { Settings } from '../../icons/index.js';
import { PillButton } from '../common/PillButton.js';
import './SidebarFooter.css';

export const SidebarFooter: React.FC = () => {
  return (
    <div className="sidebar-footer">
      <button className="sidebar-footer-settings" title="Settings">
        <Settings size={16} />
      </button>
      <PillButton variant="outlined">Upgrade</PillButton>
    </div>
  );
};
