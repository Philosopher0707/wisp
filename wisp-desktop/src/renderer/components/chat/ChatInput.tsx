import React from 'react';
import { useAppState } from '../../state/context.js';
import { InputToolbar } from './InputToolbar.js';
import { MentionPopup } from './MentionPopup.js';
import './ChatInput.css';

export const ChatInput: React.FC = () => {
  const { state, dispatch, sendMessage } = useAppState();
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const [isDragging, setIsDragging] = React.useState(false);
  const dragCounter = React.useRef(0);
  const [mentionQuery, setMentionQuery] = React.useState<string | null>(null);
  const [mentionRange, setMentionRange] = React.useState<{ start: number; end: number } | null>(null);

  const detectMention = (value: string, cursorPos: number) => {
    const beforeCursor = value.slice(0, cursorPos);
    const atIdx = beforeCursor.lastIndexOf('@');
    if (atIdx === -1) {
      setMentionQuery(null);
      setMentionRange(null);
      return;
    }
    // Only match if @ is at start, after space, or after newline
    const charBefore = atIdx > 0 ? beforeCursor[atIdx - 1] : ' ';
    if (charBefore !== ' ' && charBefore !== '\n') {
      setMentionQuery(null);
      setMentionRange(null);
      return;
    }
    const query = beforeCursor.slice(atIdx + 1);
    // Don't trigger on spaces in query
    if (query.includes(' ')) {
      setMentionQuery(null);
      setMentionRange(null);
      return;
    }
    setMentionQuery(query);
    setMentionRange({ start: atIdx, end: cursorPos });
  };

  const handleMentionSelect = (path: string) => {
    if (!mentionRange) return;
    const before = state.inputValue.slice(0, mentionRange.start);
    const after = state.inputValue.slice(mentionRange.end);
    const newValue = before + path + ' ' + after;
    dispatch({ type: 'SET_INPUT', value: newValue });
    setMentionQuery(null);
    setMentionRange(null);
    textareaRef.current?.focus();
  };

  const submitPrompt = () => {
    const trimmed = state.inputValue.trim();
    if (!trimmed) return;
    if (state.connection !== 'connected') {
      dispatch({
        type: 'RECEIVE_ERROR',
        message: state.connection === 'error'
          ? 'Connection error. Check your server settings.'
          : 'Not connected to server. Please wait...',
      });
      return;
    }
    setMentionQuery(null);
    setMentionRange(null);
    dispatch({ type: 'SUBMIT_MESSAGE', content: trimmed });
    sendMessage({
      type: 'prompt',
      content: trimmed,
      model: state.selectedModel || undefined,
      session_id: state.sessionId || undefined,
      show_thinking: state.showThinking,
      system_prompt: state.systemPrompt || undefined,
      temperature: parseFloat(localStorage.getItem('wisp_temperature') || '0.7'),
      permission_mode: state.permissionMode,
      plan_mode: state.planMode,
    });
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    dispatch({ type: 'SET_INPUT', value });
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
    }
    detectMention(value, e.target.selectionStart);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // If mention popup is open, let it handle arrow keys/enter/escape
    if (mentionQuery !== null) {
      if (['ArrowDown', 'ArrowUp', 'Enter', 'Escape'].includes(e.key)) {
        return; // Let MentionPopup's keydown handler take over
      }
    }
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
      if (f.path) paths.push(f.path);
    }

    if (paths.length > 0) {
      const current = state.inputValue;
      const fileList = paths.join('\n');
      const newValue = current ? current + '\n' + fileList : fileList;
      dispatch({ type: 'SET_INPUT', value: newValue });
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
        {mentionQuery !== null && state.workspacePath && (
          <MentionPopup
            query={mentionQuery}
            workspacePath={state.workspacePath}
            serverUrl={state.serverUrl}
            apiKey={state.apiKey}
            onSelect={handleMentionSelect}
            onClose={() => { setMentionQuery(null); setMentionRange(null); }}
          />
        )}
        <textarea
          ref={textareaRef}
          className="chat-input-textarea"
          placeholder={
            state.connection === 'connected'
              ? "Ask Wisp anything. @ to reference files"
              : state.connection === 'connecting'
                ? "Connecting to server..."
                : "Disconnected — check server settings"
          }
          value={state.inputValue}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={state.connection !== 'connected'}
        />
        <InputToolbar hasContent={state.inputValue.length > 0} onSubmit={submitPrompt} />
      </div>
    </div>
  );
};
