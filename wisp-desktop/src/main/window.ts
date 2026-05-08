import { BrowserWindow, shell } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function parseRendererArgs(): { server?: string; apiKey?: string } {
  const argv = process.argv.slice(2);
  const result: { server?: string; apiKey?: string } = {};
  for (let i = 0; i < argv.length; i++) {
    if ((argv[i] === '--server' || argv[i] === '-s') && i + 1 < argv.length) {
      result.server = argv[++i];
    } else if (argv[i] === '--api-key' && i + 1 < argv.length) {
      result.apiKey = argv[++i];
    }
  }
  if (!result.server) result.server = process.env.WISP_SERVER || 'http://localhost:8000';
  if (!result.apiKey) result.apiKey = process.env.WISP_API_KEY || '';
  return result;
}

export function createMainWindow(): BrowserWindow {
  const { server, apiKey } = parseRendererArgs();

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
      sandbox: false,
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
  if (server) queryParams.set('server', server);
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
