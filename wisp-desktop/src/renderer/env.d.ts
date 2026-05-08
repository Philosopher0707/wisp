/// <reference types="vite/client" />

interface Window {
  wisp: {
    platform: string;
    onMenuAction: (callback: (action: string) => void) => () => void;
    openFileDialog: () => Promise<string[] | null>;
    openInVSCode: (workspacePath: string) => Promise<boolean>;
    selectDirectory: () => Promise<string | null>;
  };
}
