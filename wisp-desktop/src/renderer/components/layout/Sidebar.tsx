import React from 'react';
import { SidebarNav } from '../sidebar/SidebarNav.js';
import { PinnedSection } from '../sidebar/PinnedSection.js';
import { ProjectsSection } from '../sidebar/ProjectsSection.js';
import { SidebarFooter } from '../sidebar/SidebarFooter.js';
import './Sidebar.css';

export const Sidebar: React.FC = () => {
  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <SidebarNav />
        <PinnedSection />
        <ProjectsSection />
      </div>
      <SidebarFooter />
    </aside>
  );
};
