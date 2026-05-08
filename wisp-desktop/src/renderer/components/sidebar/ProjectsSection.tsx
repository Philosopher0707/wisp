import React from 'react';
import { ProjectFolder } from './ProjectFolder.js';
import { Pin, ArrowUpDown, Folder } from '../../icons/index.js';
import './SidebarSections.css';

const defaultProjects = [
  { id: 'proj-wisp', name: 'wisp' },
  { id: 'proj-smart-memory', name: 'smart-memory-mcp' },
  { id: 'proj-aegis', name: 'aegis-extension' },
];

export const ProjectsSection: React.FC = () => {
  return (
    <div className="sidebar-section">
      <div className="sidebar-section-header">
        <span>Projects</span>
        <div className="sidebar-section-header-icons">
          <Pin size={13} />
          <ArrowUpDown size={13} />
          <Folder size={13} />
        </div>
      </div>
      <div className="sidebar-section-items">
        {defaultProjects.map((p) => (
          <ProjectFolder key={p.id} id={p.id} name={p.name} />
        ))}
      </div>
    </div>
  );
};
