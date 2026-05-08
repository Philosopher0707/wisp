import { app, BrowserWindow, ipcMain, dialog, shell } from 'electron';
import { exec } from 'child_process';
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
    const cmd = `code "${workspacePath}"`;
    return new Promise((resolve) => {
      exec(cmd, (err) => {
        if (err) {
          // Fallback: try opening with shell
          shell.openPath(workspacePath).then(() => resolve(true)).catch(() => resolve(false));
        } else {
          resolve(true);
        }
      });
    });
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
