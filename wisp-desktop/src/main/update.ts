import { BrowserWindow, dialog } from 'electron';
import { autoUpdater } from 'electron-updater';

const DELAY_MS = 10_000;

export function initAutoUpdater(mainWindow: BrowserWindow): void {
  if (process.env.WISP_AUTO_UPDATE === 'false') {
    console.log('[updater] Auto-update skipped (WISP_AUTO_UPDATE=false)');
    return;
  }

  autoUpdater.on('checking-for-update', () => {
    console.log('[updater] Checking for update...');
    mainWindow.webContents.send('updater:status', { status: 'checking-for-update' });
  });

  autoUpdater.on('update-available', (info) => {
    console.log('[updater] Update available:', info.version);
    mainWindow.webContents.send('updater:status', { status: 'update-available', version: info.version });
  });

  autoUpdater.on('update-not-available', (info) => {
    console.log('[updater] Update not available:', info.version);
    mainWindow.webContents.send('updater:status', { status: 'update-not-available', version: info.version });
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log('[updater] Update downloaded:', info.version);
    mainWindow.webContents.send('updater:status', { status: 'update-downloaded', version: info.version });

    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Available',
      message: 'A new version of Wisp is available.',
      detail: 'Restart the app to apply the update.',
      buttons: ['Restart Now', 'Later'],
      defaultId: 0,
    }).then((result) => {
      if (result.response === 0) {
        autoUpdater.quitAndInstall();
      }
    });
  });

  autoUpdater.on('error', (err) => {
    console.error('[updater] Error:', err.message);
    mainWindow.webContents.send('updater:status', { status: 'error', message: err.message });
  });

  autoUpdater.on('download-progress', (progress) => {
    console.log('[updater] Download progress:', Math.round(progress.percent));
    mainWindow.webContents.send('updater:status', {
      status: 'download-progress',
      percent: Math.round(progress.percent),
    });
  });

  setTimeout(() => {
    autoUpdater.checkForUpdatesAndNotify().catch((err: Error) => {
      console.error('[updater] Failed to check for updates:', err.message);
    });
  }, DELAY_MS);
}

export { autoUpdater };
