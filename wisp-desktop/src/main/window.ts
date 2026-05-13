import { BrowserWindow, shell } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

interface WindowOpts {
  serverUrl: string;
  apiKey: string;
}

export function createMainWindow(opts: WindowOpts): BrowserWindow {
  const { serverUrl, apiKey } = opts;

  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#111111',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.on('ready-to-show', () => {
    win.show();
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  const queryParams = new URLSearchParams();
  queryParams.set('server', serverUrl);
  if (apiKey) queryParams.set('api_key', apiKey);

  if (process.env.ELECTRON_RENDERER_URL) {
    const url = process.env.ELECTRON_RENDERER_URL;
    const sep = url.includes('?') ? '&' : '?';
    win.loadURL(url + sep + queryParams.toString());
  } else {
    win.loadFile(path.join(__dirname, '../renderer/index.html'), {
      query: Object.fromEntries(queryParams),
    });
  }

  return win;
}
