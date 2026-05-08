import React from 'react';
import { useAppState } from '../state/context.js';
import { renderMarkdown } from '../utils/markdown.js';
import './PlanPanel.css';

export const PlanPanel: React.FC = () => {
  const { state, dispatch, sendMessage } = useAppState();
  const [editing, setEditing] = React.useState(false);
  const [editValue, setEditValue] = React.useState('');
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  const planContent = state.pendingPlan || '';

  const handleEdit = () => {
    setEditValue(planContent);
    setEditing(true);
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const handleApprove = () => {
    const plan = editing ? editValue : planContent;
    dispatch({ type: 'APPROVE_PLAN', planContext: plan });
    sendMessage({
      type: 'prompt',
      content: `Execute the approved plan:\n\n${plan}`,
      model: state.selectedModel,
      session_id: state.sessionId || undefined,
      show_thinking: state.showThinking,
      system_prompt: state.systemPrompt || undefined,
      temperature: parseFloat(localStorage.getItem('wisp_temperature') || '0.7'),
      permission_mode: state.permissionMode,
      plan_mode: false,
      plan_context: plan,
    });
  };

  const handleReject = () => {
    dispatch({ type: 'REJECT_PLAN' });
  };

  if (!state.pendingPlan) return null;

  return (
    <div className="plan-overlay">
      <div className="plan-panel">
        <div className="plan-header">
          <h2 className="plan-title">Implementation Plan</h2>
          <span className="plan-subtitle">Review and approve before execution</span>
        </div>
        <div className="plan-body">
          {editing ? (
            <textarea
              ref={textareaRef}
              className="plan-editor"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              rows={20}
            />
          ) : (
            <div className="plan-content">
              {renderMarkdown(planContent)}
            </div>
          )}
        </div>
        <div className="plan-actions">
          {editing ? (
            <>
              <button className="plan-btn plan-btn--cancel" onClick={() => setEditing(false)}>Back</button>
              <button className="plan-btn plan-btn--approve" onClick={handleApprove}>Approve Edited Plan</button>
            </>
          ) : (
            <>
              <button className="plan-btn plan-btn--reject" onClick={handleReject}>Reject</button>
              <button className="plan-btn plan-btn--edit" onClick={handleEdit}>Edit</button>
              <button className="plan-btn plan-btn--approve" onClick={handleApprove}>Approve & Execute</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
