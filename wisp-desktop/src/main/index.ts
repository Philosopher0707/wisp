import { app, BrowserWindow, ipcMain, dialog, shell } from 'electron';
import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { createMainWindow } from './window.js';
import { buildMenu } from './menu.js';

let mainWindow: ReturnType<typeof createMainWindow> | null = null;

function registerIpcHandlers(): void {
  ipcMain.handle('dialog:openFile', async () => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile', 'multiSelections'],
      title: 'Select files to attach',
    });
    return result.canceled ? null : result.filePaths;
  });

  ipcMain.handle('dialog:openDirectory', async () => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
      title: 'Select Workspace Directory',
    });
    return result.canceled ? null : result.filePaths[0] || null;
  });

  ipcMain.handle('code:open', async (_e, workspacePath: string) => {
    return new Promise((resolve) => {
      execFile('code', [workspacePath], (err) => {
        if (err) {
          // Fallback: try opening with shell
          shell.openPath(workspacePath).then(() => resolve(true)).catch(() => resolve(false));
        } else {
          resolve(true);
        }
      });
    });
  });

  ipcMain.handle('dialog:openTheme', async () => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile', 'multiSelections'],
      filters: [{ name: 'Theme JSON', extensions: ['json'] }],
      title: 'Select custom theme file',
    });
    return result.canceled ? null : result.filePaths;
  });

  ipcMain.on('themes:list', (event) => {
    const themesDir = path.join(os.homedir(), '.wisp', 'themes');
    try {
      const files = fs.readdirSync(themesDir);
      event.returnValue = files
        .filter((f) => f.endsWith('.json'))
        .map((f) => path.join(themesDir, f));
    } catch {
      event.returnValue = [];
    }
  });

  ipcMain.handle('file:readDataUrl', async (_e, filePath: string) => {
    try {
      const ext = path.extname(filePath).toLowerCase();
      const mimeMap: Record<string, string> = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.bmp': 'image/bmp',
        '.svg': 'image/svg+xml',
      };
      const mime = mimeMap[ext];
      if (!mime) return null;
      const data = fs.readFileSync(filePath);
      const b64 = data.toString('base64');
      return `data:${mime};base64,${b64}`;
    } catch {
      return null;
    }
  });
}

app.whenReady().then(() => {
  registerIpcHandlers();
  mainWindow = createMainWindow();
  buildMenu(mainWindow);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow();
      buildMenu(mainWindow);
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
