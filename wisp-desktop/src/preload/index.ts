import { contextBridge, ipcRenderer } from 'electron';

export interface WispAPI {
  platform: string;
  onMenuAction: (callback: (action: string) => void) => () => void;
  openFileDialog: () => Promise<string[] | null>;
  openThemeDialog: () => Promise<string[] | null>;
  openInVSCode: (workspacePath: string) => Promise<boolean>;
  selectDirectory: () => Promise<string | null>;
  readFileAsDataUrl: (path: string) => Promise<string | null>;
  listCustomThemes: () => string[];
  checkForUpdates: () => Promise<{ status: string; message?: string }>;
  onUpdateStatus: (callback: (status: { status: string; version?: string; percent?: number; message?: string }) => void) => () => void;
}

contextBridge.exposeInMainWorld('wisp', {
  platform: process.platform,

  onMenuAction: (callback: (action: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, action: string) => callback(action);
    ipcRenderer.on('menu:action', handler);
    return () => ipcRenderer.removeListener('menu:action', handler);
  },

  openFileDialog: () => ipcRenderer.invoke('dialog:openFile'),

  openThemeDialog: () => ipcRenderer.invoke('dialog:openTheme'),

  openInVSCode: (workspacePath: string) => ipcRenderer.invoke('code:open', workspacePath),

  selectDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),

  readFileAsDataUrl: (path: string) => ipcRenderer.invoke('file:readDataUrl', path),

  listCustomThemes: () => ipcRenderer.sendSync('themes:list'),

  checkForUpdates: () => ipcRenderer.invoke('updater:checkNow'),

  onUpdateStatus: (callback) => {
    const handler = (_event: Electron.IpcRendererEvent, payload: { status: string; version?: string; percent?: number; message?: string }) => {
      callback(payload);
    };
    ipcRenderer.on('updater:status', handler);
    return () => ipcRenderer.removeListener('updater:status', handler);
  },
} satisfies WispAPI);
