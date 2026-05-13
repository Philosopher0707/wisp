import React, { useEffect, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi } from '../../hooks/useApi.js';
import { ProjectFolder } from './ProjectFolder.js';
import { Pin, ArrowUpDown, Folder } from '../../icons/index.js';
import './SidebarSections.css';

export const ProjectsSection: React.FC = () => {
  const { state } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [projectDirs, setProjectDirs] = useState<string[]>([]);

  useEffect(() => {
    if (!state.workspacePath) return;
    // Pass empty string = root of current workspace (paths are relative to WORKSPACE_ROOT)
    api.fetchFiles('').then((data) => {
      if (!data?.items) return;
      const dirs = data.items
        .filter((item) => item.type === 'directory' && !item.name.startsWith('.'))
        .map((item) => item.name)
        .slice(0, 20);
      setProjectDirs(dirs);
    }).catch(() => {});
  }, [state.workspacePath]);

  if (projectDirs.length === 0) return null;

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
        {projectDirs.map((name) => (
          <ProjectFolder key={`proj-${name}`} id={`proj-${name}`} name={name} />
        ))}
      </div>
    </div>
  );
};
