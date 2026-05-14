import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useAppState } from '../../state/context.js';
import './CompletionGhost.css';

interface CompletionGhostProps {
  textareaRef: React.RefObject<HTMLTextAreaElement>;
}

export const CompletionGhost: React.FC<CompletionGhostProps> = ({ textareaRef }) => {
  const { state, dispatch } = useAppState();
  const [ghostText, setGhostText] = useState('');
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastContentRef = useRef('');

  const clearGhost = useCallback(() => {
    setGhostText('');
    setPosition(null);
  }, []);

  // Request completion from backend
  const fetchCompletion = useCallback(async (content: string, cursorPos: number) => {
    if (!state.serverUrl || content.length < 3) {
      clearGhost();
      return;
    }

    try {
      const base = state.serverUrl.replace(/\/$/, '');
      const params = state.apiKey ? `?api-key=${encodeURIComponent(state.apiKey)}` : '';
      const resp = await fetch(`${base}/api/complete${params}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(state.apiKey ? { Authorization: `Bearer ${state.apiKey}` } : {}),
        },
        body: JSON.stringify({
          file_content: content,
          cursor_line: 0,
          cursor_char: cursorPos,
          path: '',
          language: 'text',
        }),
      });

      if (!resp.ok) return;
      const data = await resp.json();
      const completion = data.completion || '';
      if (completion && completion.length > 0 && completion !== content.slice(cursorPos)) {
        setGhostText(completion);
      } else {
        clearGhost();
      }
    } catch {
      clearGhost();
    }
  }, [state.serverUrl, clearGhost]);

  // Debounced completion check on content change
  const checkCompletion = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const content = ta.value;
    const cursorPos = ta.selectionStart;

    if (content === lastContentRef.current) return;
    lastContentRef.current = content;

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (ta.selectionStart === cursorPos) {
        fetchCompletion(content, cursorPos);
      }
    }, 400);
  }, [textareaRef, fetchCompletion]);

  // Update ghost position based on cursor
  const updatePosition = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const cursorPos = ta.selectionStart;

    // Create a mirror span to measure text position
    const mirror = document.createElement('div');
    const style = window.getComputedStyle(ta);
    mirror.style.cssText = `
      position: absolute; visibility: hidden; white-space: pre-wrap;
      overflow-wrap: break-word; font: ${style.font}; padding: ${style.padding};
      width: ${ta.clientWidth}px; line-height: ${style.lineHeight};
      letter-spacing: ${style.letterSpacing};
    `;
    mirror.textContent = ta.value.slice(0, cursorPos);
    document.body.appendChild(mirror);

    // Add a zero-width marker to get end position
    const marker = document.createElement('span');
    marker.textContent = '|';
    mirror.appendChild(marker);

    const rect = ta.getBoundingClientRect();
    const markerRect = marker.getBoundingClientRect();
    const mirrorRect = mirror.getBoundingClientRect();

    document.body.removeChild(mirror);

    // Calculate position relative to the textarea
    const lineHeight = parseFloat(style.lineHeight) || 20;
    const scrollTop = ta.scrollTop;
    setPosition({
      top: markerRect.top - mirrorRect.top - scrollTop,
      left: markerRect.left - mirrorRect.left,
    });
  }, [textareaRef]);

  // Listen for Tab to accept, Escape to dismiss, Cmd+Right for partial accept
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape' && ghostText) {
      e.preventDefault();
      clearGhost();
      return;
    }
    if (e.key === 'Tab' && ghostText) {
      e.preventDefault();
      const ta = textareaRef.current;
      if (!ta) return;
      const before = ta.value.slice(0, ta.selectionStart);
      const after = ta.value.slice(ta.selectionEnd);
      const newValue = before + ghostText + after;
      ta.value = newValue;
      ta.selectionStart = ta.selectionEnd = before.length + ghostText.length;
      dispatch({ type: 'SET_INPUT', value: newValue });
      clearGhost();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'ArrowRight' && ghostText) {
      e.preventDefault();
      const ta = textareaRef.current;
      if (!ta) return;
      // Accept first word of ghost text
      const wordMatch = ghostText.match(/^\S+\s*/);
      if (!wordMatch) return;
      const partial = wordMatch[0];
      const before = ta.value.slice(0, ta.selectionStart);
      const after = ta.value.slice(ta.selectionEnd);
      const newValue = before + partial + after;
      ta.value = newValue;
      ta.selectionStart = ta.selectionEnd = before.length + partial.length;
      dispatch({ type: 'SET_INPUT', value: newValue });
      setGhostText(ghostText.slice(partial.length));
      if (ghostText.slice(partial.length).length === 0) clearGhost();
    }
  }, [ghostText, textareaRef, dispatch, clearGhost]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.addEventListener('keydown', handleKeyDown as EventListener);
    return () => ta.removeEventListener('keydown', handleKeyDown as EventListener);
  }, [handleKeyDown, textareaRef]);

  if (!ghostText || !position) return null;

  return (
    <div
      className="completion-ghost"
      style={{
        top: `${position.top}px`,
        left: `${position.left}px`,
      }}
    >
      <span className="completion-ghost-text">{ghostText}</span>
      <span className="completion-ghost-hint">Tab</span>
    </div>
  );
};
