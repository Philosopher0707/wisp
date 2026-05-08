import React from 'react';
import { useAppState } from '../../state/context.js';
import { InputToolbar } from './InputToolbar.js';
import './ChatInput.css';

export const ChatInput: React.FC = () => {
  const { state, dispatch, sendMessage } = useAppState();
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  const submitPrompt = () => {
    const trimmed = state.inputValue.trim();
    if (!trimmed) return;
    dispatch({ type: 'SUBMIT_MESSAGE', content: trimmed });
    sendMessage({
      type: 'prompt',
      content: trimmed,
      model: state.selectedModel || undefined,
      session_id: state.sessionId || undefined,
      show_thinking: state.showThinking,
    });
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    dispatch({ type: 'SET_INPUT', value: e.target.value });
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitPrompt();
    }
  };

  React.useEffect(() => {
    if (!state.isStreaming && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [state.isStreaming]);

  return (
    <div className="chat-input-wrapper">
      <div className="chat-input-box">
        <textarea
          ref={textareaRef}
          className="chat-input-textarea"
          placeholder="Ask Wisp anything. @ to use plugins or use files"
          value={state.inputValue}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          rows={1}
        />
        <InputToolbar hasContent={state.inputValue.length > 0} onSubmit={submitPrompt} />
      </div>
    </div>
  );
};
