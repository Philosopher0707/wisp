import { app, BrowserWindow, ipcMain, dialog } from 'electron';
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
