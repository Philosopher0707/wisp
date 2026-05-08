import React from 'react';
import { useAppState } from '../../state/context.js';
import { InputToolbar } from './InputToolbar.js';
import './ChatInput.css';

export const ChatInput: React.FC = () => {
  const { state, dispatch, sendMessage } = useAppState();
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const [isDragging, setIsDragging] = React.useState(false);
  const dragCounter = React.useRef(0);

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

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current += 1;
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current <= 0) {
      dragCounter.current = 0;
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    dragCounter.current = 0;

    const files = e.dataTransfer.files;
    if (files.length === 0) return;

    const paths: string[] = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i] as File & { path?: string };
      if (f.path) {
        paths.push(f.path);
      }
    }

    if (paths.length > 0) {
      dispatch({
        type: 'RECEIVE_STATUS',
        message: `Attached: ${paths.map((p) => p.split('/').pop()).join(', ')}`,
        level: 'info',
      });
    }
  };

  React.useEffect(() => {
    if (!state.isStreaming && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [state.isStreaming]);

  return (
    <div className="chat-input-wrapper">
      <div
        className={`chat-input-box ${isDragging ? 'chat-input-box--drag' : ''}`}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {isDragging && (
          <div className="chat-input-drop-overlay">
            Drop files to attach
          </div>
        )}
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
