import React from 'react';
import type { Message } from '../../state/types.js';
import { renderMarkdown } from '../../utils/markdown.js';
import { ToolCallBanner } from './ToolCallBanner.js';
import '../../utils/markdown.css';
import './MessageBubble.css';

interface Props {
  msg: Message;
}

export const MessageBubble: React.FC<Props> = ({ msg }) => {
  if (msg.role === 'user') {
    return (
      <div className="msg-row msg-row--user">
        <div className="msg-bubble msg-bubble--user">
          <div className="msg-content">{renderMarkdown(msg.content)}</div>
        </div>
      </div>
    );
  }

  if (msg.role === 'system') {
    return (
      <div className="msg-row msg-row--system">
        <p className="msg-system">{msg.content}</p>
      </div>
    );
  }

  return (
    <div className="msg-row msg-row--assistant">
      <div className="msg-bubble msg-bubble--assistant">
        {msg.thinking && (
          <details className="msg-thinking-details">
            <summary className="msg-thinking-toggle">Thinking...</summary>
            <p className="msg-thinking">{msg.thinking.slice(-500)}</p>
          </details>
        )}
        {msg.content && <div className="msg-content">{renderMarkdown(msg.content)}</div>}
        {msg.toolCalls?.map((tc, i) => (
          <ToolCallBanner key={i} toolCall={tc} />
        ))}
        {!msg.content && !msg.thinking && (!msg.toolCalls || msg.toolCalls.length === 0) && (
          <p className="msg-loading">Thinking...</p>
        )}
      </div>
    </div>
  );
};
