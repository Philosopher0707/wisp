import React, { useState } from 'react';
import { useAppState } from '../../state/context.js';
import { Plus, Shield, Mic, ArrowUp, Square, ChevronDown, Sparkles, History, Keyboard, Pause, Play, Lightbulb } from '../../icons/index.js';
import { ModelSelector } from './ModelSelector.js';
import { ReasoningSelector } from './ReasoningSelector.js';
import { PermissionSelector } from './PermissionSelector.js';
import type { PendingImage } from '../../state/types.js';
import './InputToolbar.css';

interface InputToolbarProps {
  hasContent: boolean;
  onSubmit: () => void;
}

export const InputToolbar: React.FC<InputToolbarProps> = ({ hasContent, onSubmit }) => {
  const { state, dispatch, sendMessage } = useAppState();
  const [injectText, setInjectText] = useState('');

  const handleAttach = async () => {
    if (window.wisp?.openFileDialog) {
      const paths = await window.wisp.openFileDialog();
      if (paths && paths.length > 0) {
        const IMG_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']);
        const imagePaths: string[] = [];
        const otherPaths: string[] = [];
        for (const p of paths) {
          const ext = p.split('.').pop()?.toLowerCase() || '';
          if (IMG_EXTS.has(ext)) imagePaths.push(p);
          else otherPaths.push(p);
        }

        // Read images via IPC
        if (imagePaths.length > 0 && window.wisp.readFileAsDataUrl) {
          const images: PendingImage[] = [];
          let idCounter = 0;
          for (const imgPath of imagePaths) {
            try {
              const dataUrl = await window.wisp.readFileAsDataUrl(imgPath);
              if (dataUrl) {
                idCounter += 1;
                images.push({
                  id: `attach-${idCounter}`,
                  dataUrl,
                  fileName: imgPath.split('/').pop() || imgPath,
                  size: 0,
                });
              }
            } catch { /* skip */ }
          }
          if (images.length > 0) {
            dispatch({ type: 'ADD_IMAGES', images });
          }
        }

        if (otherPaths.length > 0) {
          dispatch({
            type: 'RECEIVE_STATUS',
            message: `Attached: ${otherPaths.map((p) => p.split('/').pop()).join(', ')}`,
            level: 'info',
          });
        }
      }
    }
  };

  const handlePause = () => {
    sendMessage({ type: 'pause' });
    dispatch({ type: 'AGENT_PAUSED' });
  };

  const handleResume = () => {
    sendMessage({
      type: 'resume',
      injected_text: injectText.trim() || undefined,
    });
    setInjectText('');
    dispatch({ type: 'AGENT_RESUMED' });
  };

  return (
    <div className="input-toolbar">
      <div className="input-toolbar-left">
        <button className="toolbar-icon-btn" title="Attach files" onClick={handleAttach}>
          <Plus size={18} />
        </button>
        <PermissionSelector />
        <button
          className={`toolbar-icon-btn ${state.planMode ? 'toolbar-icon-btn--active' : ''}`}
          title={state.planMode ? 'Plan mode on' : 'Plan mode off'}
          onClick={() => dispatch({ type: 'TOGGLE_PLAN_MODE' })}
        >
          <Sparkles size={18} />
        </button>
        <button
          className={`toolbar-icon-btn ${state.checkpointPanelOpen ? 'toolbar-icon-btn--active' : ''}`}
          title={state.checkpointPanelOpen ? 'Hide checkpoints' : 'Show checkpoints'}
          onClick={() => dispatch({ type: 'TOGGLE_CHECKPOINT_PANEL' })}
        >
          <History size={18} />
        </button>
        <button
          className={`toolbar-icon-btn ${state.suggestionsPanelOpen ? 'toolbar-icon-btn--active' : ''}`}
          title={state.suggestionsPanelOpen ? 'Hide suggestions' : 'Show edit suggestions'}
          onClick={() => dispatch({ type: 'TOGGLE_SUGGESTIONS_PANEL' })}
        >
          <Lightbulb size={18} />
        </button>
        <button
          className={`toolbar-icon-btn ${state.vimMode ? 'toolbar-icon-btn--active' : ''}`}
          title={state.vimMode ? 'Vim mode on (Esc to disable)' : 'Vim mode off'}
          onClick={() => {
            dispatch({ type: 'TOGGLE_VIM_MODE' });
            localStorage.setItem('wisp_vim_mode', String(!state.vimMode));
          }}
        >
          <Keyboard size={18} />
        </button>
      </div>
      <div className="input-toolbar-right">
        <ModelSelector />
        <ReasoningSelector />
        {state.isStreaming ? (
          <>
            {state.agentPaused ? (
              <>
                <input
                  className="steering-inject-input"
                  type="text"
                  placeholder="Steering feedback..."
                  value={injectText}
                  onChange={(e) => setInjectText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleResume();
                    if (e.key === 'Escape') {
                      setInjectText('');
                      handleResume();
                    }
                  }}
                />
                <button className="resume-btn" title="Resume" onClick={handleResume}>
                  <Play size={14} />
                </button>
              </>
            ) : (
              <button className="pause-btn" title="Pause generation" onClick={handlePause}>
                <Pause size={14} />
              </button>
            )}
            <button
              className="stop-btn"
              title="Stop generation"
              onClick={() => {
                dispatch({ type: 'INTERRUPT' });
                sendMessage({ type: 'interrupt' });
                setInjectText('');
              }}
            >
              <Square size={14} />
            </button>
          </>
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
