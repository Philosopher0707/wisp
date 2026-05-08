import React from 'react';
import { useAppState } from '../../state/context.js';
import { Plus, Shield, Mic, ArrowUp, ChevronDown } from '../../icons/index.js';
import { ModelSelector } from './ModelSelector.js';
import { ReasoningSelector } from './ReasoningSelector.js';
import './InputToolbar.css';

interface InputToolbarProps {
  hasContent: boolean;
}

export const InputToolbar: React.FC<InputToolbarProps> = ({ hasContent }) => {
  const { state, dispatch } = useAppState();

  const handleSubmit = () => {
    const trimmed = state.inputValue.trim();
    if (trimmed) {
      dispatch({ type: 'SUBMIT_MESSAGE', content: trimmed });
    }
  };

  return (
    <div className="input-toolbar">
      <div className="input-toolbar-left">
        <button className="toolbar-icon-btn" title="Attach files">
          <Plus size={18} />
        </button>
        <button
          className="full-access-badge"
          onClick={() => dispatch({ type: 'TOGGLE_FULL_ACCESS' })}
        >
          <Shield size={13} />
          <span>{state.isFullAccessEnabled ? 'Full access' : 'Restricted'}</span>
          <ChevronDown size={10} />
        </button>
      </div>
      <div className="input-toolbar-right">
        <ModelSelector />
        <ReasoningSelector />
        <button className="toolbar-icon-btn" title="Voice input">
          <Mic size={18} />
        </button>
        <button
          className={`send-btn ${hasContent ? 'send-btn--active' : ''}`}
          title="Send"
          onClick={handleSubmit}
        >
          <ArrowUp size={16} />
        </button>
      </div>
    </div>
  );
};
