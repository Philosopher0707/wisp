import React from 'react';
import { Folder, ChevronDown } from '../../icons/index.js';
import './ProjectContextBar.css';

export const ProjectContextBar: React.FC = () => {
  return (
    <div className="project-context-bar">
      <button className="project-context-btn">
        <Folder size={13} />
        <span>wisp</span>
        <ChevronDown size={10} />
      </button>
    </div>
  );
};
