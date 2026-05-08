import { contextBridge, ipcRenderer } from 'electron';

export interface WispAPI {
  platform: string;
  onMenuAction: (callback: (action: string) => void) => () => void;
  openFileDialog: () => Promise<string[] | null>;
  openInVSCode: (workspacePath: string) => Promise<boolean>;
  selectDirectory: () => Promise<string | null>;
}

contextBridge.exposeInMainWorld('wisp', {
  platform: process.platform,

  onMenuAction: (callback: (action: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, action: string) => callback(action);
    ipcRenderer.on('menu:action', handler);
    return () => ipcRenderer.removeListener('menu:action', handler);
  },

  openFileDialog: () => ipcRenderer.invoke('dialog:openFile'),

  openInVSCode: (workspacePath: string) => ipcRenderer.invoke('code:open', workspacePath),

  selectDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
} satisfies WispAPI);
