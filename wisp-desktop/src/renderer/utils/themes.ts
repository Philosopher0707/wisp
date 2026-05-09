export interface ThemeColors {
  '--bg-app': string;
  '--bg-sidebar': string;
  '--bg-topbar': string;
  '--bg-input': string;
  '--bg-input-focus': string;
  '--bg-hover': string;
  '--bg-active': string;
  '--bg-dropdown': string;
  '--bg-badge': string;
  '--bg-send': string;
  '--bg-send-hover': string;
  '--text-primary': string;
  '--text-secondary': string;
  '--text-muted': string;
  '--text-placeholder': string;
  '--text-white': string;
  '--accent-purple': string;
  '--accent-purple-hover': string;
  '--accent-purple-text': string;
  '--accent-orange': string;
  '--accent-orange-text': string;
  '--font-sans': string;
  '--font-mono': string;
  '--font-scale': string;
  '--text-xs': string;
  '--text-sm': string;
  '--text-base': string;
  '--text-md': string;
  '--text-lg': string;
  '--text-xl': string;
  '--text-2xl': string;
  '--space-1': string;
  '--space-2': string;
  '--space-3': string;
  '--space-4': string;
  '--space-5': string;
  '--space-6': string;
  '--space-8': string;
  '--space-10': string;
  '--radius-sm': string;
  '--radius-md': string;
  '--radius-lg': string;
  '--radius-xl': string;
  '--radius-full': string;
  '--sidebar-width': string;
  '--sidebar-collapsed-width': string;
  '--topbar-height': string;
  '--input-max-width': string;
  '--traffic-light-offset': string;
  '--shadow-dropdown': string;
  '--shadow-button': string;
  '--transition-fast': string;
  '--transition-normal': string;
}

export const DARK_THEME: ThemeColors = {
  '--bg-app': '#111111',
  '--bg-sidebar': '#0d0d0d',
  '--bg-topbar': '#111111',
  '--bg-input': '#1e1e1e',
  '--bg-input-focus': '#252525',
  '--bg-hover': '#1a1a1a',
  '--bg-active': '#252525',
  '--bg-dropdown': '#1c1c1c',
  '--bg-badge': '#2a2a2a',
  '--bg-send': '#444444',
  '--bg-send-hover': '#555555',
  '--text-primary': '#f0f0f0',
  '--text-secondary': '#999999',
  '--text-muted': '#666666',
  '--text-placeholder': '#777777',
  '--text-white': '#ffffff',
  '--accent-purple': '#8b5cf6',
  '--accent-purple-hover': '#7c3aed',
  '--accent-purple-text': '#c4b5fd',
  '--accent-orange': '#f59e0b',
  '--accent-orange-text': '#fcd34d',
  '--font-sans': "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', sans-serif",
  '--font-mono': "'SF Mono', 'Fira Code', monospace",
  '--font-scale': '1',
  '--text-xs': 'calc(11px * var(--font-scale))',
  '--text-sm': 'calc(12px * var(--font-scale))',
  '--text-base': 'calc(13px * var(--font-scale))',
  '--text-md': 'calc(14px * var(--font-scale))',
  '--text-lg': 'calc(16px * var(--font-scale))',
  '--text-xl': 'calc(20px * var(--font-scale))',
  '--text-2xl': 'calc(32px * var(--font-scale))',
  '--space-1': '4px',
  '--space-2': '8px',
  '--space-3': '12px',
  '--space-4': '16px',
  '--space-5': '20px',
  '--space-6': '24px',
  '--space-8': '32px',
  '--space-10': '40px',
  '--radius-sm': '6px',
  '--radius-md': '8px',
  '--radius-lg': '12px',
  '--radius-xl': '18px',
  '--radius-full': '9999px',
  '--sidebar-width': '240px',
  '--sidebar-collapsed-width': '52px',
  '--topbar-height': '44px',
  '--input-max-width': '720px',
  '--traffic-light-offset': '78px',
  '--shadow-dropdown': '0 8px 30px rgba(0, 0, 0, 0.6)',
  '--shadow-button': '0 1px 3px rgba(0, 0, 0, 0.3)',
  '--transition-fast': '120ms ease',
  '--transition-normal': '200ms ease',
};

export const LIGHT_THEME: ThemeColors = {
  '--bg-app': '#ffffff',
  '--bg-sidebar': '#f5f5f5',
  '--bg-topbar': '#ffffff',
  '--bg-input': '#f0f0f0',
  '--bg-input-focus': '#e8e8e8',
  '--bg-hover': '#f0f0f0',
  '--bg-active': '#e8e8e8',
  '--bg-dropdown': '#ffffff',
  '--bg-badge': '#e8e8e8',
  '--bg-send': '#cccccc',
  '--bg-send-hover': '#bbbbbb',
  '--text-primary': '#1a1a1a',
  '--text-secondary': '#666666',
  '--text-muted': '#999999',
  '--text-placeholder': '#aaaaaa',
  '--text-white': '#000000',
  '--accent-purple': '#7c3aed',
  '--accent-purple-hover': '#6d28d9',
  '--accent-purple-text': '#5b21b6',
  '--accent-orange': '#d97706',
  '--accent-orange-text': '#92400e',
  '--font-sans': "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', sans-serif",
  '--font-mono': "'SF Mono', 'Fira Code', monospace",
  '--font-scale': '1',
  '--text-xs': 'calc(11px * var(--font-scale))',
  '--text-sm': 'calc(12px * var(--font-scale))',
  '--text-base': 'calc(13px * var(--font-scale))',
  '--text-md': 'calc(14px * var(--font-scale))',
  '--text-lg': 'calc(16px * var(--font-scale))',
  '--text-xl': 'calc(20px * var(--font-scale))',
  '--text-2xl': 'calc(32px * var(--font-scale))',
  '--space-1': '4px',
  '--space-2': '8px',
  '--space-3': '12px',
  '--space-4': '16px',
  '--space-5': '20px',
  '--space-6': '24px',
  '--space-8': '32px',
  '--space-10': '40px',
  '--radius-sm': '6px',
  '--radius-md': '8px',
  '--radius-lg': '12px',
  '--radius-xl': '18px',
  '--radius-full': '9999px',
  '--sidebar-width': '240px',
  '--sidebar-collapsed-width': '52px',
  '--topbar-height': '44px',
  '--input-max-width': '720px',
  '--traffic-light-offset': '78px',
  '--shadow-dropdown': '0 8px 30px rgba(0, 0, 0, 0.12)',
  '--shadow-button': '0 1px 3px rgba(0, 0, 0, 0.1)',
  '--transition-fast': '120ms ease',
  '--transition-normal': '200ms ease',
};

export function applyTheme(colors: Partial<ThemeColors>): void {
  Object.entries(colors).forEach(([key, value]) => {
    if (value !== undefined) {
      document.documentElement.style.setProperty(key, value);
    }
  });
}

export function loadCustomTheme(path: string): ThemeColors | null {
  try {
    // For local file:// protocol, use fetch with electron's custom protocol or read via IPC
    // We attempt fetch first, which works if the file is served or uses file://
    const xhr = new XMLHttpRequest();
    xhr.open('GET', path, false); // synchronous for simplicity
    xhr.send();
    if (xhr.status === 200 || xhr.status === 0) {
      const data = JSON.parse(xhr.responseText);
      return { ...DARK_THEME, ...data };
    }
    return null;
  } catch {
    return null;
  }
}

export async function loadCustomThemeAsync(path: string): Promise<ThemeColors | null> {
  try {
    const resp = await fetch(path);
    if (resp.ok) {
      const data = await resp.json();
      return { ...DARK_THEME, ...data };
    }
    return null;
  } catch {
    return null;
  }
}

export function discoverThemes(): Array<{ name: string; path: string; isBuiltin: boolean }> {
  const themes: Array<{ name: string; path: string; isBuiltin: boolean }> = [
    { name: 'Dark', path: 'builtin:dark', isBuiltin: true },
    { name: 'Light', path: 'builtin:light', isBuiltin: true },
  ];

  // Check if electron API is available for scanning custom themes directory
  if (typeof window !== 'undefined' && (window as any).wisp?.listCustomThemes) {
    try {
      const customThemes: string[] = (window as any).wisp.listCustomThemes();
      customThemes.forEach((p) => {
        const name = p.split('/').pop()?.replace('.json', '') || p;
        themes.push({ name, path: p, isBuiltin: false });
      });
    } catch { /* ignore */ }
  }

  return themes;
}
