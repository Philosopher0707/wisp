import React from 'react';
import { useAppState } from '../../state/context.js';
import { ChevronDown } from '../../icons/index.js';
import { Dropdown } from '../common/Dropdown.js';

function modelLabel(name: string): string {
  return name
    .split(/[-:]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

export const ModelSelector: React.FC = () => {
  const { state, dispatch } = useAppState();
  const isOpen = state.activeDropdown === 'model';

  const models = state.availableModels.length > 0
    ? state.availableModels.map((m) => ({ value: m, label: modelLabel(m) }))
    : [{ value: state.selectedModel, label: modelLabel(state.selectedModel) }];

  const currentLabel = models.find((m) => m.value === state.selectedModel)?.label || modelLabel(state.selectedModel);

  return (
    <div className="selector-wrapper">
      <button
        className={`toolbar-dropdown-btn ${isOpen ? 'toolbar-dropdown-btn--open' : ''}`}
        onClick={() =>
          dispatch({ type: isOpen ? 'CLOSE_DROPDOWN' : 'OPEN_DROPDOWN', id: 'model' })
        }
      >
        <span>{currentLabel}</span>
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
