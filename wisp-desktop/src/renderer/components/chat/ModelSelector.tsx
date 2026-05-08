import React from 'react';
import { useAppState } from '../../state/context.js';
import { ChevronDown } from '../../icons/index.js';
import { Dropdown } from '../common/Dropdown.js';

const models = [
  { value: 'claude-opus-4-7', label: 'Claude Opus 4.7' },
  { value: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
  { value: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
  { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
];

export const ModelSelector: React.FC = () => {
  const { state, dispatch } = useAppState();
  const isOpen = state.activeDropdown === 'model';

  return (
    <div className="selector-wrapper">
      <button
        className={`toolbar-dropdown-btn ${isOpen ? 'toolbar-dropdown-btn--open' : ''}`}
        onClick={() =>
          dispatch({ type: isOpen ? 'CLOSE_DROPDOWN' : 'OPEN_DROPDOWN', id: 'model' })
        }
      >
        <span>{models.find((m) => m.value === state.selectedModel)?.label || state.selectedModel}</span>
        <ChevronDown size={10} />
      </button>
      {isOpen && (
        <Dropdown
          options={models}
          selected={state.selectedModel}
          onSelect={(value) => dispatch({ type: 'SET_MODEL', model: value })}
          onClose={() => dispatch({ type: 'CLOSE_DROPDOWN' })}
        />
      )}
    </div>
  );
};
