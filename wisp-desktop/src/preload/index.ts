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
} satisfies WispAPI);
