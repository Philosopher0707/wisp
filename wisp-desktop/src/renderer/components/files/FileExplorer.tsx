import React, { useEffect, useState, useCallback } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi, type FileItem } from '../../hooks/useApi.js';
import { X, Folder, FolderOpen, File, ChevronRight, ChevronDown } from '../../icons/index.js';
import { highlightCode } from '../../utils/markdown.js';
import './FileExplorer.css';

interface TreeState {
  expanded: Set<string>;
  children: Map<string, FileItem[]>;
  loading: Set<string>;
}

function getFileLang(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    py: 'python', rs: 'rust', go: 'go', java: 'java', c: 'c', cpp: 'cpp',
    h: 'c', hpp: 'cpp', css: 'css', html: 'xml', json: 'json', yaml: 'yaml',
    yml: 'yaml', md: 'markdown', sql: 'sql', sh: 'bash', bash: 'bash',
    toml: 'ini', xml: 'xml', svg: 'xml', rb: 'ruby', php: 'php',
    swift: 'swift', kt: 'kotlin', scala: 'scala', r: 'r',
  };
  return map[ext] || '';
}

function emptyTree(): TreeState {
  return { expanded: new Set(), children: new Map(), loading: new Set() };
}

export const FileExplorer: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);

  const [rootItems, setRootItems] = useState<FileItem[]>([]);
  const [tree, setTree] = useState<TreeState>(emptyTree);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  const loadRoot = useCallback(() => {
    api.fetchFiles('').then((data) => {
      if (data?.type === 'directory' && data.items) {
        setRootItems(data.items);
      }
    }).catch(() => {});
  }, [api]);

  // Load root on mount and when workspace changes
  useEffect(() => {
    loadRoot();
    setTree(emptyTree());
    setSelectedPath(null);
    setFileContent(null);
  }, [state.workspacePath, loadRoot]);

  // Auto-open file from QuickFileModal (Cmd+P)
  useEffect(() => {
    if (state.selectedFilePath) {
      openFile(state.selectedFilePath);
    }
  }, [state.selectedFilePath]);

  const handleOpenFolder = useCallback(async () => {
    let dirPath: string | null = null;

    if (window.wisp?.selectDirectory) {
      dirPath = await window.wisp.selectDirectory();
    } else {
      // Browser fallback: prompt for path
      dirPath = prompt('Enter workspace directory path:', state.workspacePath || '/Users/');
    }

    if (!dirPath) return;

    const baseUrl = state.serverUrl.replace(/\/$/, '');
    const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';

    try {
      const resp = await fetch(`${baseUrl}/api/workspace${params}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: dirPath }),
      });
      if (resp.ok) {
        const data = await resp.json() as { path?: string };
        if (data.path) {
          dispatch({ type: 'SET_WORKSPACE', path: data.path });
        }
      }
    } catch { /* ignore */ }
  }, [state.serverUrl, state.apiKey, state.workspacePath, dispatch]);

  const toggleDir = useCallback(async (dirPath: string) => {
    const next = new Set(tree.expanded);
    const nextChildren = new Map(tree.children);

    if (next.has(dirPath)) {
      next.delete(dirPath);
      setTree({ ...tree, expanded: next, children: nextChildren });
      return;
    }

    next.add(dirPath);

    if (!nextChildren.has(dirPath)) {
      const nextLoading = new Set(tree.loading);
      nextLoading.add(dirPath);
      setTree({ expanded: next, children: nextChildren, loading: nextLoading });

      const data = await api.fetchFiles(dirPath);
      nextLoading.delete(dirPath);

      if (data?.type === 'directory' && data.items) {
        nextChildren.set(dirPath, data.items);
      }
      setTree({ expanded: next, children: nextChildren, loading: nextLoading });
      return;
    }

    setTree({ ...tree, expanded: next, children: nextChildren });
  }, [tree, api]);

  const openFile = useCallback(async (filePath: string) => {
    setSelectedPath(filePath);
    setLoadingContent(true);
    setFileContent(null);
    const data = await api.fetchFiles(filePath);
    if (data?.type === 'file' && data.content != null) {
      setFileContent(data.content);
    }
    setLoadingContent(false);
  }, [api]);

  const handleClose = () => {
    dispatch({ type: 'TOGGLE_RIGHT_PANEL' });
  };

  const renderItem = (item: FileItem, depth: number) => {
    const isDir = item.type === 'directory';
    const isExpanded = tree.expanded.has(item.path);
    const isLoading = tree.loading.has(item.path);
    const children = tree.children.get(item.path) || [];
    const isSelected = item.path === selectedPath;

    return (
      <div key={item.path}>
        <div
          className={`fe-item ${isSelected ? 'fe-item--selected' : ''}`}
          style={{ paddingLeft: `${8 + depth * 16}px` }}
          onClick={() => {
            if (isDir) {
              toggleDir(item.path);
            } else {
              openFile(item.path);
            }
          }}
        >
          {isDir && (
            <span className="fe-chevron">
              {isLoading ? (
                <span className="fe-spinner" />
              ) : isExpanded ? (
                <ChevronDown size={12} />
              ) : (
                <ChevronRight size={12} />
              )}
            </span>
          )}
          {isDir ? (
            isExpanded ? <FolderOpen size={15} className="fe-icon fe-icon--folder" /> :
              <Folder size={15} className="fe-icon fe-icon--folder" />
          ) : (
            <File size={14} className="fe-icon fe-icon--file" />
          )}
          <span className="fe-name">{item.name}</span>
          {item.size != null && <span className="fe-size">{formatSize(item.size)}</span>}
        </div>
        {isDir && isExpanded && children.map((child) => renderItem(child, depth + 1))}
      </div>
    );
  };

  const wsLabel = state.workspacePath
    ? state.workspacePath.split('/').pop() || state.workspacePath
    : 'Workspace';

  return (
    <aside className="file-explorer">
      <div className="fe-header">
        <span className="fe-title">{wsLabel}</span>
        <div className="fe-header-actions">
          <button
            className="fe-open-folder-btn"
            onClick={handleOpenFolder}
            title="Open folder..."
          >
            <Folder size={14} />
          </button>
          <button className="fe-close-btn" onClick={handleClose} title="Close">
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="fe-body">
        <div className="fe-tree">
          {rootItems.length === 0 && (
            <p className="fe-empty">Loading files...</p>
          )}
          {rootItems.map((item) => renderItem(item, 0))}
        </div>
        {fileContent != null && (
          <div className="fe-viewer">
            <div className="fe-viewer-header">
              <span className="fe-viewer-title">{selectedPath}</span>
              <button
                className="fe-viewer-close"
                onClick={() => { setFileContent(null); setSelectedPath(null); }}
              >
                <X size={12} />
              </button>
            </div>
            <pre className="fe-viewer-code">
              <code
                dangerouslySetInnerHTML={{
                  __html: highlightCode(fileContent, getFileLang(selectedPath || '')),
                }}
              />
            </pre>
          </div>
        )}
        {loadingContent && (
          <div className="fe-viewer">
            <p className="fe-empty">Loading...</p>
          </div>
        )}
      </div>
    </aside>
  );
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
