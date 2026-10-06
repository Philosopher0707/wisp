import { contextBridge, ipcRenderer } from 'electron';

export interface BackendStatus {
  running: boolean;
  url: string;
  managed: boolean;
  pid: number | null;
  port: number;
}

export interface BrowserState {
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  error: string | null;
}

export interface WispBrowserAPI {
  navigate: (input: string) => Promise<{ ok: boolean; url?: string; reason?: string }>;
  setBounds: (rect: { x: number; y: number; width: number; height: number }) => void;
  setVisible: (visible: boolean) => void;
  action: (action: 'back' | 'forward' | 'reload' | 'stop') => void;
  getState: () => Promise<BrowserState | null>;
  onState: (callback: (state: BrowserState) => void) => () => void;
}

export interface WispAPI {
  browser: WispBrowserAPI;
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
  getBackendStatus: () => Promise<BackendStatus>;
}

contextBridge.exposeInMainWorld('wisp', {
  platform: process.platform,

  browser: {
    navigate: (input: string) => ipcRenderer.invoke('browser:navigate', input),
    setBounds: (rect) => ipcRenderer.send('browser:bounds', rect),
    setVisible: (visible: boolean) => ipcRenderer.send('browser:visible', visible),
    action: (action) => ipcRenderer.send('browser:action', action),
    getState: () => ipcRenderer.invoke('browser:state'),
    onState: (callback) => {
      const handler = (_e: Electron.IpcRendererEvent, state: BrowserState) => callback(state);
      ipcRenderer.on('browser:state', handler);
      return () => ipcRenderer.removeListener('browser:state', handler);
    },
  } satisfies WispBrowserAPI,

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

  getBackendStatus: () => ipcRenderer.invoke('backend:status'),
} satisfies WispAPI);
