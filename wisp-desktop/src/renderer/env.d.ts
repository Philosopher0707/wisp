/// <reference types="vite/client" />

import type { WispAPI } from '../preload/index.js';

/**
 * The renderer's view of the preload bridge.
 *
 * Derived from the preload's own `WispAPI` rather than restated, because the two
 * had drifted: this file listed 5 members while `src/preload/index.ts` exposes
 * 11, so `window.wisp.readFileAsDataUrl` (used by the image-attach path in
 * `InputToolbar`) was a type error even though the method exists at runtime.
 * A restated contract cannot stay in sync; a derived one cannot drift.
 */
declare global {
  interface Window {
    wisp: WispAPI;
    __settingsTab?: string;
  }
}
