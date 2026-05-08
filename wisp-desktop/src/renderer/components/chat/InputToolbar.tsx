import React from 'react';
import { useAppState } from '../../state/context.js';
import { Plus, Shield, Mic, ArrowUp, Square, ChevronDown } from '../../icons/index.js';
import { ModelSelector } from './ModelSelector.js';
import { ReasoningSelector } from './ReasoningSelector.js';
import './InputToolbar.css';

interface InputToolbarProps {
  hasContent: boolean;
  onSubmit: () => void;
}

export const InputToolbar: React.FC<InputToolbarProps> = ({ hasContent, onSubmit }) => {
  const { state, dispatch, sendMessage } = useAppState();

  const handleAttach = async () => {
    if (window.wisp?.openFileDialog) {
      const paths = await window.wisp.openFileDialog();
      if (paths && paths.length > 0) {
        dispatch({
          type: 'RECEIVE_STATUS',
          message: `Attached: ${paths.map((p) => p.split('/').pop()).join(', ')}`,
          level: 'info',
        });
      }
    }
  };

  return (
    <div className="input-toolbar">
      <div className="input-toolbar-left">
        <button className="toolbar-icon-btn" title="Attach files" onClick={handleAttach}>
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
        {state.isStreaming ? (
          <button
            className="stop-btn"
            title="Stop generation"
            onClick={() => {
              dispatch({ type: 'INTERRUPT' });
              sendMessage({ type: 'interrupt' });
            }}
          >
            <Square size={14} />
          </button>
        ) : (
          <>
            <button className="toolbar-icon-btn" title="Voice input">
              <Mic size={18} />
            </button>
            <button
              className={`send-btn ${hasContent ? 'send-btn--active' : ''}`}
              title="Send"
              onClick={onSubmit}
            >
              <ArrowUp size={16} />
            </button>
          </>
        )}
      </div>
    </div>
  );
};
