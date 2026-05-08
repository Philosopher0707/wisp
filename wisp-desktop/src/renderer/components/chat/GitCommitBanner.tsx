import React from 'react';
import { GitBranch } from '../../icons/index.js';
import './GitCommitBanner.css';

interface GitCommitBannerProps {
  branch: string;
  changedFiles: string[];
  onCommit: () => void;
  onDismiss: () => void;
  committing: boolean;
}

export const GitCommitBanner: React.FC<GitCommitBannerProps> = ({
  branch,
  changedFiles,
  onCommit,
  onDismiss,
  committing,
}) => {
  const [expanded, setExpanded] = React.useState(false);

  return (
    <div className="git-banner">
      <div className="git-banner-main">
        <GitBranch size={14} />
        <span className="git-banner-text">
          {changedFiles.length} file{changedFiles.length !== 1 ? 's' : ''} changed on <strong>{branch}</strong>
        </span>
        <button className="git-banner-toggle" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Hide' : 'Show'}
        </button>
        <div className="git-banner-spacer" />
        <button className="git-banner-dismiss" onClick={onDismiss}>Dismiss</button>
        <button className="git-banner-commit" onClick={onCommit} disabled={committing}>
          {committing ? 'Committing...' : 'Commit All'}
        </button>
      </div>
      {expanded && (
        <div className="git-banner-files">
          {changedFiles.map((f, i) => (
            <div key={i} className="git-banner-file">{f}</div>
          ))}
        </div>
      )}
    </div>
  );
};
