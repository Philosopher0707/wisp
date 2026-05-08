import { app, BrowserWindow } from 'electron';
import { createMainWindow } from './window.js';
import { buildMenu } from './menu.js';

let mainWindow: ReturnType<typeof createMainWindow> | null = null;

app.whenReady().then(() => {
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
