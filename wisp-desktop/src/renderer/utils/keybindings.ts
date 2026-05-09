export const DEFAULT_KEYBINDINGS: Record<string, string> = {
  'search': 'Cmd+Shift+F',
  'quickfile': 'Cmd+P',
  'settings': 'Cmd+,',
  'shortcuts': 'Cmd+/',
  'newChat': 'Cmd+N',
  'toggleSidebar': 'Cmd+B',
  'toggleRightPanel': 'Cmd+Shift+E',
  'convSearch': 'Cmd+F',
  'clearChat': 'Cmd+L',
  'toggleThinking': 'Cmd+T',
  'fontIncrease': 'Cmd+=',
  'fontDecrease': 'Cmd+-',
  'fontReset': 'Cmd+0',
  'interrupt': 'Cmd+C',
  'planMode': 'Cmd+Shift+P',
  'inlineEdit': 'Cmd+K',
};

export function loadKeybindings(): Record<string, string> {
  try {
    const stored = localStorage.getItem('wisp_keybindings');
    if (stored) {
      const parsed = JSON.parse(stored);
      // Merge with defaults so new actions always have a fallback
      return { ...DEFAULT_KEYBINDINGS, ...parsed };
    }
  } catch { /* ignore corrupt data */ }
  return { ...DEFAULT_KEYBINDINGS };
}

export function saveKeybindings(bindings: Record<string, string>): void {
  localStorage.setItem('wisp_keybindings', JSON.stringify(bindings));
}

export function formatShortcut(shortcut: string): string {
  const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;

  return shortcut
    .replace(/Cmd/g, isMac ? '⌘' : 'Ctrl')
    .replace(/Ctrl/g, 'Ctrl')
    .replace(/Shift/g, isMac ? '⇧' : 'Shift')
    .replace(/Alt/g, isMac ? '⌥' : 'Alt')
    .replace(/Plus/g, '+')
    .replace(/Minus/g, '-')
    .replace(/\+/g, '');
}

export function buildShortcutMap(keybindings: Record<string, string>): Map<string, string> {
  // Maps shortcut string → action name for reverse lookup
  const map = new Map<string, string>();
  Object.entries(keybindings).forEach(([action, shortcut]) => {
    map.set(shortcut, action);
  });
  return map;
}

export function parseShortcut(shortcut: string): {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
} {
  const parts = shortcut.split('+');
  const key = parts[parts.length - 1].toLowerCase();
  const normalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();

  return {
    key: key === 'plus' ? '=' :
         key === 'minus' ? '-' :
         key === '/' ? '/' :
         key === ',' ? ',' :
         key === '0' ? '0' :
         key,
    metaKey: parts.some((p) => normalize(p) === 'Cmd'),
    ctrlKey: parts.some((p) => normalize(p) === 'Ctrl'),
    shiftKey: parts.some((p) => normalize(p) === 'Shift'),
    altKey: parts.some((p) => normalize(p) === 'Alt'),
  };
}

export function matchShortcut(e: KeyboardEvent, shortcut: string): boolean {
  const parsed = parseShortcut(shortcut);
  const eKey = e.key.toLowerCase();

  // Special handling for plus/equals key
  const expectedKey = parsed.key;
  if (expectedKey === '=' && eKey !== '=' && eKey !== '+') return false;
  if (expectedKey !== '=' && eKey !== expectedKey) return false;

  if (parsed.metaKey !== (e.metaKey || e.ctrlKey)) return false;
  if (parsed.shiftKey !== e.shiftKey) {
    // Cmd+= sends shiftKey=true because = is Shift+= on US keyboards
    // So allow either way for the = shortcut
    if (!(expectedKey === '=' && e.shiftKey)) return false;
  }
  if (parsed.altKey !== e.altKey) return false;
  if (parsed.ctrlKey && !e.metaKey && parsed.ctrlKey !== e.ctrlKey) return false;

  return true;
}

export function findMatchingAction(
  e: KeyboardEvent,
  keybindings: Record<string, string>,
): string | null {
  for (const [action, shortcut] of Object.entries(keybindings)) {
    if (matchShortcut(e, shortcut)) {
      return action;
    }
  }
  return null;
}
