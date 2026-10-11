import { WebContentsView, ipcMain, type BrowserWindow } from 'electron';
import { isAllowedNavigation, normalizeBrowserUrl } from './browser-url.js';

export interface BrowserState {
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  error: string | null;
}

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * The dock's Browser tab. A native WebContentsView owned by the main process, positioned over a placeholder the
 * renderer measures. It is isolated on purpose: its own persistent partition, no preload, sandboxed, every permission
 * denied, and only http(s) navigation, so a page opened here cannot reach the app's backend key or IPC.
 */
export function registerBrowserView(win: BrowserWindow): void {
  let view: WebContentsView | null = null;
  let bounds: Rect = { x: 0, y: 0, width: 0, height: 0 };
  let visible = false;
  let error: string | null = null;

  const snapshot = (): BrowserState => {
    const wc = view?.webContents;
    return {
      url: wc?.getURL() ?? '',
      title: wc?.getTitle() ?? '',
      loading: wc?.isLoading() ?? false,
      canGoBack: wc?.navigationHistory.canGoBack() ?? false,
      canGoForward: wc?.navigationHistory.canGoForward() ?? false,
      error,
    };
  };
  const publish = () => {
    if (!win.isDestroyed()) win.webContents.send('browser:state', snapshot());
  };
  const place = () => {
    if (!view) return;
    const show = visible && bounds.width > 0 && bounds.height > 0;
    view.setVisible(show);
    if (show) view.setBounds(bounds);
  };

  const ensure = (): WebContentsView => {
    if (view) return view;
    view = new WebContentsView({
      webPreferences: { partition: 'persist:wisp-browser', sandbox: true, contextIsolation: true, nodeIntegration: false },
    });
    const wc = view.webContents;
    wc.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    wc.setWindowOpenHandler(({ url }) => {
      if (isAllowedNavigation(url)) void wc.loadURL(url);
      return { action: 'deny' };
    });
    const guard = (event: Electron.Event, url: string) => {
      if (!isAllowedNavigation(url)) event.preventDefault();
    };
    wc.on('will-navigate', guard);
    wc.on('will-redirect', guard);
    for (const name of ['did-start-loading', 'did-stop-loading', 'did-navigate', 'did-navigate-in-page', 'page-title-updated'] as const) {
      wc.on(name as 'did-stop-loading', () => {
        if (name === 'did-start-loading') error = null;
        publish();
      });
    }
    wc.on('did-fail-load', (_e, code, description, _url, isMainFrame) => {
      if (isMainFrame && code !== -3) {
        error = `${description} (${code})`;
        publish();
      }
    });
    win.contentView.addChildView(view);
    place();
    return view;
  };

  ipcMain.handle('browser:navigate', async (event, input: string) => {
    if (event.sender !== win.webContents) return { ok: false, reason: 'unauthorized' };
    const decision = normalizeBrowserUrl(String(input ?? ''));
    if (!decision.ok) {
      error = decision.reason;
      publish();
      return decision;
    }
    error = null;
    try {
      await ensure().webContents.loadURL(decision.url);
    } catch {
      // did-fail-load already reported it
    }
    return decision;
  });

  ipcMain.on('browser:bounds', (event, rect: Rect) => {
    if (event.sender !== win.webContents) return;
    const n = (v: unknown) => (Number.isFinite(v) ? Math.max(0, Math.round(v as number)) : 0);
    bounds = { x: n(rect?.x), y: n(rect?.y), width: n(rect?.width), height: n(rect?.height) };
    place();
  });

  ipcMain.on('browser:visible', (event, next: boolean) => {
    if (event.sender !== win.webContents) return;
    visible = Boolean(next);
    place();
  });

  ipcMain.on('browser:action', (event, action: 'back' | 'forward' | 'reload' | 'stop') => {
    if (event.sender !== win.webContents || !view) return;
    const wc = view.webContents;
    if (action === 'back' && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
    else if (action === 'forward' && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward();
    else if (action === 'reload') wc.reload();
    else if (action === 'stop') wc.stop();
  });

  ipcMain.handle('browser:state', (event) => (event.sender === win.webContents ? snapshot() : null));

  win.on('closed', () => {
    for (const ch of ['browser:navigate', 'browser:state']) ipcMain.removeHandler(ch);
    for (const ch of ['browser:bounds', 'browser:visible', 'browser:action']) ipcMain.removeAllListeners(ch);
    if (view && !view.webContents.isDestroyed()) view.webContents.close();
    view = null;
  });
}
