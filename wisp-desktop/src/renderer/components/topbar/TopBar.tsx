import React from 'react';
import { Sparkles, User, Folder, Square } from '../../icons/index.js';
import { PillButton } from '../common/PillButton.js';
import { IconButton } from '../common/IconButton.js';
import './TopBar.css';

export const TopBar: React.FC = () => {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <span className="topbar-label">New chat</span>
        <PillButton variant="filled" icon={Sparkles} color="purple">
          Get Plus
        </PillButton>
      </div>
      <div className="topbar-right">
        <IconButton icon={User} size={18} title="Account" />
        <IconButton icon={Folder} size={18} title="Files" />
        <IconButton icon={Square} size={18} title="New Window" />
      </div>
    </header>
  );
};
