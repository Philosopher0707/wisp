import React from 'react';
import { useAppState } from '../../state/context.js';
import { InputToolbar } from './InputToolbar.js';
import { MentionPopup } from './MentionPopup.js';
import { ContextIndicator } from './ContextIndicator.js';
import { CompletionGhost } from './CompletionGhost.js';
import { VimEditor } from '../../utils/vim.js';
import { ImagePreview } from './ImagePreview.js';
import type { PendingImage } from '../../state/types.js';
import './ChatInput.css';

export const ChatInput: React.FC = () => {
  const { state, dispatch, sendMessage } = useAppState();
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const vimRef = React.useRef(new VimEditor('normal'));
  const [isDragging, setIsDragging] = React.useState(false);
  const dragCounter = React.useRef(0);
  const [mentionQuery, setMentionQuery] = React.useState<string | null>(null);
  const [mentionRange, setMentionRange] = React.useState<{ start: number; end: number } | null>(null);
  const [vimModeDisplay, setVimModeDisplay] = React.useState(vimRef.current.getStatusLine());

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
    const imageUrls = state.pendingImages.map((img) => img.dataUrl);
    sendMessage({
      type: 'prompt',
      content: trimmed,
      images: imageUrls.length > 0 ? imageUrls : undefined,
      model: state.selectedModel || undefined,
      session_id: state.sessionId || undefined,
      show_thinking: state.showThinking,
      system_prompt: state.systemPrompt || undefined,
      temperature: parseFloat(localStorage.getItem('wisp_temperature') || '0.7'),
      permission_mode: state.permissionMode,
      plan_mode: state.planMode,
    });
    dispatch({ type: 'CLEAR_IMAGES' });
  };

  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']);
  const IMAGE_MIME_PREFIX = 'image/';
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024; // 5MB

  let _imgCounter = 0;
  function nextImgId(): string { _imgCounter += 1; return `img-${_imgCounter}`; }

  function readFileAsDataUrl(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      if (file.size > MAX_IMAGE_BYTES) {
        reject(new Error(`Image too large: ${(file.size / 1024 / 1024).toFixed(1)}MB (max 5MB)`));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(new Error('Failed to read file'));
      reader.readAsDataURL(file);
    });
  }

  async function filesToImages(files: FileList | File[]): Promise<PendingImage[]> {
    const results: PendingImage[] = [];
    for (const f of files) {
      const ext = f.name.split('.').pop()?.toLowerCase() || '';
      const isImage = f.type.startsWith(IMAGE_MIME_PREFIX) || IMAGE_EXTENSIONS.has(ext);
      if (isImage) {
        try {
          const dataUrl = await readFileAsDataUrl(f);
          results.push({ id: nextImgId(), dataUrl, fileName: f.name, size: f.size });
        } catch (err: any) {
          dispatch({ type: 'RECEIVE_ERROR', message: err.message || 'Failed to read image' });
        }
      }
    }
    return results;
  }

  const ghostRef = React.useRef<any>(null);

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imageFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith(IMAGE_MIME_PREFIX)) {
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault();
      filesToImages(imageFiles).then((images) => {
        if (images.length > 0) {
          dispatch({ type: 'ADD_IMAGES', images });
        }
      });
    }
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
    // Vim mode handling
    if (state.vimMode && textareaRef.current) {
      const nativeEvent = e.nativeEvent;
      const handled = vimRef.current.handleKeyDown(nativeEvent, textareaRef.current);
      setVimModeDisplay(vimRef.current.getStatusLine());

      // After vim changes, sync React state
      if (handled) {
        dispatch({ type: 'SET_INPUT', value: textareaRef.current.value });

        // If vim exited insert mode via Escape, also close any mention popup
        if (vimRef.current.mode === 'normal' && mentionQuery !== null) {
          setMentionQuery(null);
          setMentionRange(null);
        }
      }
    }

    // If mention popup is open, let it handle arrow keys/enter/escape
    if (mentionQuery !== null) {
      if (['ArrowDown', 'ArrowUp', 'Enter', 'Escape'].includes(e.key)) {
        return; // Let MentionPopup's keydown handler take over
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitPrompt();
      // Reset vim to normal mode on submit
      if (state.vimMode) {
        vimRef.current.mode = 'normal';
        setVimModeDisplay(vimRef.current.getStatusLine());
      }
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

    filesToImages(files).then((images) => {
      if (images.length > 0) {
        dispatch({ type: 'ADD_IMAGES', images });
      }
      // Remaining non-image files: insert paths as text
      const paths: string[] = [];
      for (let i = 0; i < files.length; i++) {
        const f = files[i] as File & { path?: string };
        const ext = f.name.split('.').pop()?.toLowerCase() || '';
        if (!f.type.startsWith(IMAGE_MIME_PREFIX) && !IMAGE_EXTENSIONS.has(ext) && f.path) {
          paths.push(f.path);
        }
      }
      if (paths.length > 0) {
        const current = state.inputValue;
        const fileList = paths.join('\n');
        const newValue = current ? current + '\n' + fileList : fileList;
        dispatch({ type: 'SET_INPUT', value: newValue });
      }
    });
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
          className={`chat-input-textarea ${state.vimMode ? 'chat-input-textarea--vim' : ''}`}
          placeholder={
            state.connection === 'connected'
              ? state.vimMode
                ? "NORMAL mode -- Press i/a/o to type, Esc to normal"
                : "Ask Wisp anything. @ to reference files"
              : state.connection === 'connecting'
                ? "Connecting to server..."
                : "Disconnected — check server settings"
          }
          value={state.inputValue}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          rows={1}
          disabled={state.connection !== 'connected'}
        />
        <ImagePreview />
        <CompletionGhost textareaRef={textareaRef} />
        {state.vimMode && (
          <div className="chat-input-vim-status">
            {vimModeDisplay}
          </div>
        )}
        <InputToolbar hasContent={state.inputValue.length > 0} onSubmit={submitPrompt} />
        <ContextIndicator />
      </div>
    </div>
  );
};
