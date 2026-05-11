import React from 'react';

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
  }

  handleReload = () => {
    window.location.reload();
  };

  handleCopyError = async () => {
    const { error } = this.state;
    if (!error) return;
    const text = `${error.name}: ${error.message}\n\n${error.stack || ''}`;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
  };

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const errorMessage = this.state.error
      ? `${this.state.error.name}: ${this.state.error.message}`.slice(0, 200)
      : 'Unknown error';

    const containerStyle: React.CSSProperties = {
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '100vh',
      padding: 'var(--space-6)',
      background: 'var(--bg-app)',
      color: 'var(--text-primary)',
      fontFamily: 'var(--font-sans)',
      textAlign: 'center',
    };

    const cardStyle: React.CSSProperties = {
      maxWidth: '480px',
      width: '100%',
      padding: 'var(--space-6)',
      background: 'var(--bg-sidebar)',
      borderRadius: 'var(--radius-lg)',
      border: '1px solid var(--bg-hover)',
    };

    const titleStyle: React.CSSProperties = {
      fontSize: 'var(--text-xl)',
      fontWeight: 600,
      marginBottom: 'var(--space-4)',
      color: 'var(--text-primary)',
    };

    const messageStyle: React.CSSProperties = {
      fontSize: 'var(--text-sm)',
      color: 'var(--text-secondary)',
      marginBottom: 'var(--space-5)',
      wordBreak: 'break-word',
      fontFamily: 'var(--font-mono)',
      background: 'var(--bg-input)',
      padding: 'var(--space-3)',
      borderRadius: 'var(--radius-md)',
      textAlign: 'left',
    };

    const buttonGroupStyle: React.CSSProperties = {
      display: 'flex',
      gap: 'var(--space-3)',
      justifyContent: 'center',
      flexWrap: 'wrap',
    };

    const buttonBaseStyle: React.CSSProperties = {
      padding: 'var(--space-2) var(--space-4)',
      borderRadius: 'var(--radius-md)',
      border: 'none',
      fontSize: 'var(--text-sm)',
      fontWeight: 500,
      cursor: 'pointer',
      transition: 'background var(--transition-fast)',
      fontFamily: 'var(--font-sans)',
    };

    const reloadButtonStyle: React.CSSProperties = {
      ...buttonBaseStyle,
      background: 'var(--accent-purple)',
      color: '#fff',
    };

    const copyButtonStyle: React.CSSProperties = {
      ...buttonBaseStyle,
      background: 'var(--bg-active)',
      color: 'var(--text-primary)',
    };

    const resetButtonStyle: React.CSSProperties = {
      ...buttonBaseStyle,
      background: 'transparent',
      color: 'var(--text-secondary)',
      border: '1px solid var(--bg-hover)',
    };

    return (
      <div style={containerStyle}>
        <div style={cardStyle}>
          <div style={titleStyle}>Something went wrong</div>
          <div style={messageStyle}>{errorMessage}</div>
          <div style={buttonGroupStyle}>
            <button style={reloadButtonStyle} onClick={this.handleReload} type="button">
              Reload App
            </button>
            <button style={copyButtonStyle} onClick={this.handleCopyError} type="button">
              Copy Error
            </button>
            <button style={resetButtonStyle} onClick={this.handleReset} type="button">
              Try Again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
