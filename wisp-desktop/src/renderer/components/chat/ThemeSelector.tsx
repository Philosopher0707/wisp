import React from 'react';
import { useAppState } from '../../state/context.js';
import { ChevronDown } from '../../icons/index.js';
import { Dropdown } from '../common/Dropdown.js';
import { applyTheme, DARK_THEME, LIGHT_THEME, loadCustomThemeAsync, discoverThemes, ThemeColors } from '../../utils/themes.js';
import './ThemeSelector.css';

function applyBuiltinTheme(mode: 'dark' | 'light'): void {
  const colors: Partial<ThemeColors> = mode === 'dark' ? DARK_THEME : LIGHT_THEME;
  applyTheme(colors);
}

export const ThemeSelector: React.FC = () => {
  const { state, dispatch } = useAppState();
  const isOpen = state.activeDropdown === 'theme';

  const themes = state.availableThemes.length > 0
    ? state.availableThemes
    : discoverThemes();

  const currentThemeName = themes.find((t) => {
    if (state.theme === 'custom' && state.customThemePath) {
      return t.path === state.customThemePath;
    }
    return t.path === `builtin:${state.theme}`;
  })?.name || (state.theme === 'dark' ? 'Dark' : state.theme === 'light' ? 'Light' : 'Custom');

  const themeOptions = [
    ...themes.map((t) => ({
      value: t.path,
      label: t.name,
    })),
    { value: '__browse__', label: 'Browse custom theme...' },
  ];

  const selectedValue = (() => {
    if (state.theme === 'custom' && state.customThemePath) {
      return state.customThemePath;
    }
    return `builtin:${state.theme}`;
  })();

  const handleSelect = async (value: string) => {
    if (value === '__browse__') {
      dispatch({ type: 'CLOSE_DROPDOWN' });
      if ((window as any).wisp?.openThemeDialog) {
        try {
          const paths: string[] | null = await (window as any).wisp.openThemeDialog();
          if (paths && paths.length > 0) {
            const themeData = await loadCustomThemeAsync(paths[0]);
            if (themeData) {
              applyTheme(themeData);
              dispatch({ type: 'SET_THEME', theme: 'custom', customPath: paths[0] });
            }
          }
        } catch { /* user cancelled */ }
      }
      return;
    }

    if (value.startsWith('builtin:')) {
      const mode = value.replace('builtin:', '') as 'dark' | 'light';
      applyBuiltinTheme(mode);
      dispatch({ type: 'SET_THEME', theme: mode });
    } else {
      const themeData = await loadCustomThemeAsync(value);
      if (themeData) {
        applyTheme(themeData);
        dispatch({ type: 'SET_THEME', theme: 'custom', customPath: value });
      }
    }
  };

  // Apply theme on initial render
  React.useEffect(() => {
    const storedTheme = localStorage.getItem('wisp_theme') as 'dark' | 'light' | 'custom' | null;
    const storedPath = localStorage.getItem('wisp_custom_theme_path');

    if (storedTheme) {
      if (storedTheme === 'dark') {
        applyBuiltinTheme('dark');
      } else if (storedTheme === 'light') {
        applyBuiltinTheme('light');
      } else if (storedTheme === 'custom' && storedPath) {
        loadCustomThemeAsync(storedPath).then((data) => {
          if (data) applyTheme(data);
          else applyBuiltinTheme('dark'); // fallback
        });
      }
    }

    // Discover and set available themes
    const discovered = discoverThemes();
    dispatch({ type: 'SET_AVAILABLE_THEMES', themes: discovered });
  }, []);

  return (
    <div className="selector-wrapper">
      <button
        className={`toolbar-dropdown-btn ${isOpen ? 'toolbar-dropdown-btn--open' : ''}`}
        onClick={() =>
          dispatch({ type: isOpen ? 'CLOSE_DROPDOWN' : 'OPEN_DROPDOWN', id: 'theme' })
        }
      >
        <span>{currentThemeName}</span>
        <ChevronDown size={10} />
      </button>
      {isOpen && (
        <Dropdown
          options={themeOptions}
          selected={selectedValue}
          onSelect={handleSelect}
          onClose={() => dispatch({ type: 'CLOSE_DROPDOWN' })}
        />
      )}
    </div>
  );
};
