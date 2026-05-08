import React from 'react';
import { useAppState } from '../state/context.js';
import './ApprovalPrompt.css';

export const ApprovalPrompt: React.FC = () => {
  const { state, dispatch, sendMessage } = useAppState();
  if (!state.approvalPending) return null;

  const { callId, toolName, args, reason } = state.approvalPending;
  const preview = String(
    args.path || args.command || args.content || '...',
  ).slice(0, 80);

  return (
    <div className="approval-overlay">
      <div className="approval-card">
        <div className="approval-header">
          <span className="approval-icon">&#9888;</span>
          <span className="approval-title">Approve dangerous action?</span>
        </div>
        <p className="approval-reason">{reason}</p>
        <code className="approval-code">
          {toolName}({preview})
        </code>
        <div className="approval-actions">
          <button
            className="approval-btn approval-btn--deny"
            onClick={() => {
              dispatch({ type: 'DENY_TOOL', callId });
              sendMessage({
                type: 'tool_approval',
                id: callId,
                approved: false,
                reason: 'User denied',
              });
            }}
          >
            Deny
          </button>
          <button
            className="approval-btn approval-btn--approve"
            onClick={() => {
              dispatch({ type: 'APPROVE_TOOL', callId });
              sendMessage({
                type: 'tool_approval',
                id: callId,
                approved: true,
              });
            }}
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  );
};
