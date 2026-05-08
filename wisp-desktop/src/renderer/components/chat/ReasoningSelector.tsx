import React from 'react';
import { useAppState } from '../../state/context.js';
import { ChevronDown, SlidersHorizontal } from '../../icons/index.js';
import { Dropdown } from '../common/Dropdown.js';

const levels = [
  { value: 'low', label: 'Low reasoning' },
  { value: 'medium', label: 'Medium reasoning' },
  { value: 'high', label: 'High reasoning' },
];

export const ReasoningSelector: React.FC = () => {
  const { state, dispatch } = useAppState();
  const isOpen = state.activeDropdown === 'reasoning';

  return (
    <div className="selector-wrapper">
      <button
        className={`toolbar-dropdown-btn ${isOpen ? 'toolbar-dropdown-btn--open' : ''}`}
        onClick={() =>
          dispatch({ type: isOpen ? 'CLOSE_DROPDOWN' : 'OPEN_DROPDOWN', id: 'reasoning' })
        }
      >
        <SlidersHorizontal size={14} />
        <span>{state.reasoningLevel.charAt(0).toUpperCase() + state.reasoningLevel.slice(1)}</span>
        <ChevronDown size={10} />
      </button>
      {isOpen && (
        <Dropdown
          options={levels}
          selected={state.reasoningLevel}
          onSelect={(value) =>
            dispatch({ type: 'SET_REASONING', level: value as 'low' | 'medium' | 'high' })
          }
          onClose={() => dispatch({ type: 'CLOSE_DROPDOWN' })}
        />
      )}
    </div>
  );
};
