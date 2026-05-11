import React from 'react';

interface Props {
  children: React.ReactNode;
  onNewChat: () => void;
}

interface State {
  hasError: boolean;
}

export class ChatErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ChatErrorBoundary caught an error:', error, errorInfo);
  }

  handleNewChat = () => {
    this.props.onNewChat();
    this.setState({ hasError: false });
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const containerStyle: React.CSSProperties = {
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      flex: 1,
      padding: 'var(--space-6)',
      background: 'var(--bg-app)',
      color: 'var(--text-primary)',
      fontFamily: 'var(--font-sans)',
      textAlign: 'center',
      gap: 'var(--space-4)',
    };

    const messageStyle: React.CSSProperties = {
      fontSize: 'var(--text-base)',
      color: 'var(--text-secondary)',
      maxWidth: '360px',
      lineHeight: 1.5,
    };

    const buttonStyle: React.CSSProperties = {
      padding: 'var(--space-2) var(--space-4)',
      borderRadius: 'var(--radius-md)',
      border: 'none',
      fontSize: 'var(--text-sm)',
      fontWeight: 500,
      cursor: 'pointer',
      transition: 'background var(--transition-fast)',
      fontFamily: 'var(--font-sans)',
      background: 'var(--accent-purple)',
      color: '#fff',
    };

    return (
      <div style={containerStyle}>
        <div style={messageStyle}>
          Something went wrong in this conversation. Try starting a new chat.
        </div>
        <button style={buttonStyle} onClick={this.handleNewChat} type="button">
          New Chat
        </button>
      </div>
    );
  }
}
