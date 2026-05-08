import { contextBridge } from 'electron';

contextBridge.exposeInMainWorld('wisp', {
  platform: process.platform,
});
