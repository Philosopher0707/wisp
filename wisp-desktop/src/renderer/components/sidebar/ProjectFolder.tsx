import React from 'react';
import { useAppState } from '../../state/context.js';
import { ChevronDown } from '../../icons/index.js';
import { ChatListItem } from './ChatListItem.js';
import './SidebarSections.css';

interface ProjectFolderProps {
  id: string;
  name: string;
}

export const ProjectFolder: React.FC<ProjectFolderProps> = ({ id, name }) => {
  const { state, dispatch } = useAppState();
  const expanded = state.sidebarExpandedProjects.has(id);

  return (
    <div className="project-folder">
      <button
        className="project-folder-header"
        onClick={() => dispatch({ type: 'TOGGLE_PROJECT_FOLDER', projectId: id })}
      >
        <ChevronDown
          size={12}
          className={`project-folder-chevron ${expanded ? 'project-folder-chevron--open' : ''}`}
        />
        <span>{name}</span>
      </button>
      {expanded && (
        <div className="project-folder-children">
          <ChatListItem />
          <ChatListItem />
        </div>
      )}
    </div>
  );
};
