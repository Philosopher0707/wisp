import React from 'react';
import hljs from 'highlight.js';

function highlightCode(code: string, lang?: string): string {
  if (!code.trim()) return '';
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value;
    }
    return hljs.highlightAuto(code).value;
  } catch {
    return code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
}

const CodeBlock: React.FC<{ code: string; lang: string }> = ({ code, lang }) => {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // fallback for older Electron
      const ta = document.createElement('textarea');
      ta.value = code;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <pre className="md-code-block">
      {lang && <div className="md-code-lang">{lang}</div>}
      <button
        className={`md-copy-btn ${copied ? 'md-copy-btn--copied' : ''}`}
        onClick={handleCopy}
        title="Copy code"
      >
        {copied ? 'Copied!' : 'Copy'}
      </button>
      <code
        dangerouslySetInnerHTML={{ __html: highlightCode(code, lang) }}
      />
    </pre>
  );
};

export function renderMarkdown(text: string): React.ReactNode {
  if (!text) return text;

  const lines = text.split('\n');
  const result: React.ReactNode[] = [];

  let inCodeBlock = false;
  let codeContent = '';
  let codeLang = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code block start/end
    if (line.trimStart().startsWith('```')) {
      if (inCodeBlock) {
        result.push(
          <CodeBlock key={`cb-${i}`} code={codeContent.replace(/\n$/, '')} lang={codeLang} />,
        );
        codeContent = '';
        codeLang = '';
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
        codeLang = line.trimStart().slice(3).trim();
      }
      continue;
    }

    if (inCodeBlock) {
      codeContent += line + '\n';
      continue;
    }

    // Inline formatting
    let processed = line;

    // Inline code (before bold/italic)
    processed = processed.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');

    // Bold
    processed = processed.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    // Italic
    processed = processed.replace(/\*([^*]+)\*/g, '<em>$1</em>');

    // Headings
    const hMatch = processed.match(/^(#{1,3})\s+(.+)/);
    if (hMatch) {
      const level = hMatch[1].length;
      const size = level === 1 ? 'var(--text-xl)' : level === 2 ? 'var(--text-lg)' : 'var(--text-md)';
      result.push(
        <p
          key={`h-${i}`}
          className="md-heading"
          style={{ fontSize: size, fontWeight: 600, marginTop: 'var(--space-2)' }}
          dangerouslySetInnerHTML={{ __html: hMatch[2] }}
        />,
      );
      continue;
    }

    // Blockquote
    if (processed.startsWith('&gt; ')) {
      result.push(
        <blockquote key={`bq-${i}`} className="md-blockquote"
          dangerouslySetInnerHTML={{ __html: processed.slice(5) }}
        />,
      );
      continue;
    }

    // Unordered list
    if (/^[\s]*[-*+]\s+/.test(processed)) {
      result.push(
        <li key={`li-${i}`} className="md-li"
          dangerouslySetInnerHTML={{ __html: processed.replace(/^[\s]*[-*+]\s+/, '') }}
        />,
      );
      continue;
    }

    // Ordered list
    const olMatch = processed.match(/^[\s]*(\d+)\.\s+(.+)/);
    if (olMatch) {
      result.push(
        <li key={`ol-${i}`} className="md-li"
          dangerouslySetInnerHTML={{ __html: olMatch[2] }}
        />,
      );
      continue;
    }

    // Empty line
    if (processed.trim() === '') {
      result.push(<br key={`br-${i}`} />);
      continue;
    }

    // Regular paragraph
    result.push(
      <p key={`p-${i}`} dangerouslySetInnerHTML={{ __html: processed }} />,
    );
  }

  // Unclosed code block
  if (inCodeBlock && codeContent) {
    result.push(
      <CodeBlock key="cb-end" code={codeContent.replace(/\n$/, '')} lang={codeLang} />,
    );
  }

  return <>{result}</>;
}
