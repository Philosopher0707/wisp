import React from 'react';
import hljs from 'highlight.js';
import { useAppState } from '../state/context.js';
import { DiffPreview } from '../components/chat/DiffPreview.js';

export function highlightCode(code: string, lang?: string): string {
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

function guessFilePath(lang: string, workspacePath: string): string {
  if (!workspacePath) return '';
  const extMap: Record<string, string> = {
    typescript: '.ts', tsx: '.tsx', javascript: '.js', jsx: '.jsx',
    python: '.py', rust: '.rs', go: '.go', java: '.java', css: '.css',
    html: '.html', json: '.json', yaml: '.yml', markdown: '.md',
    sql: '.sql', sh: '.sh', bash: '.sh', toml: '.toml', xml: '.xml',
    ruby: '.rb', php: '.php', swift: '.swift', kotlin: '.kt',
  };
  const ext = extMap[lang] || `.${lang}` || '';
  return `${workspacePath}/new_file${ext}`;
}

const CodeBlock: React.FC<{ code: string; lang: string }> = ({ code, lang }) => {
  const [copied, setCopied] = React.useState(false);
  const [applying, setApplying] = React.useState(false);
  const [applyPath, setApplyPath] = React.useState('');
  const [applyStatus, setApplyStatus] = React.useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [applyMsg, setApplyMsg] = React.useState('');
  const inputRef = React.useRef<HTMLInputElement>(null);

  const { state } = useAppState();
  const serverUrl = state.serverUrl;
  const apiKey = state.apiKey;
  const workspacePath = state.workspacePath;

  const isRunnable = /^(sh|bash|shell|zsh)$/.test(lang);

  const [running, setRunning] = React.useState(false);
  const [runOutput, setRunOutput] = React.useState<{ stdout: string; stderr: string; exitCode: number } | null>(null);
  const [showOutput, setShowOutput] = React.useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
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

  const handleRun = async () => {
    setRunning(true);
    setShowOutput(true);
    setRunOutput(null);
    try {
      const base = serverUrl.replace(/\/$/, '');
      const params = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';
      const body = JSON.stringify({ command: code, cwd: workspacePath || '.' });
      const resp = await fetch(`${base}/api/bash${params}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        setRunOutput({ stdout: '', stderr: (err as { detail?: string }).detail || resp.statusText, exitCode: 1 });
      } else {
        const data = await resp.json() as { stdout: string; stderr: string; exit_code: number };
        setRunOutput({ stdout: data.stdout, stderr: data.stderr, exitCode: data.exit_code });
      }
    } catch (err) {
      setRunOutput({ stdout: '', stderr: err instanceof Error ? err.message : 'Run failed', exitCode: 1 });
    } finally {
      setRunning(false);
    }
  };

  const startApply = () => {
    setApplying(true);
    setApplyStatus('idle');
    setApplyMsg('');
    const guess = guessFilePath(lang, workspacePath);
    setApplyPath(guess);
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const cancelApply = () => {
    setApplying(false);
    setApplyPath('');
    setApplyStatus('idle');
  };

  const [diffData, setDiffData] = React.useState<{ diff: string; isNew: boolean } | null>(null);
  const [diffPath, setDiffPath] = React.useState('');

  const submitApply = async () => {
    const fp = applyPath.trim();
    if (!fp) return;

    let relPath = fp;
    if (workspacePath && fp.startsWith(workspacePath)) {
      relPath = fp.slice(workspacePath.length).replace(/^\//, '');
    }

    setApplyStatus('saving');
    try {
      const base = serverUrl.replace(/\/$/, '');
      const params = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';

      // Fetch diff preview
      const diffResp = await fetch(`${base}/api/diff${params}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: relPath, new_content: code }),
      });
      if (!diffResp.ok) {
        const err = await diffResp.json().catch(() => ({ detail: diffResp.statusText }));
        throw new Error((err as { detail?: string }).detail || diffResp.statusText);
      }
      const d = await diffResp.json() as { diff: string; is_new: boolean; path: string };
      setDiffData({ diff: d.diff, isNew: d.is_new });
      setDiffPath(relPath);
      setApplyStatus('idle');
    } catch (err) {
      setApplyStatus('error');
      setApplyMsg(err instanceof Error ? err.message : 'Failed to generate diff');
    }
  };

  const confirmApply = async () => {
    if (!diffPath) return;
    setApplyStatus('saving');
    try {
      const base = serverUrl.replace(/\/$/, '');
      const params = apiKey ? `?api-key=${encodeURIComponent(apiKey)}` : '';
      const qs = `${params}${params ? '&' : '?'}path=${encodeURIComponent(diffPath)}`;
      const resp = await fetch(`${base}/api/files${qs}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: code }),
      });
      if (resp.ok) {
        setApplyStatus('saved');
        setApplyMsg(`Written to ${diffPath}`);
        setDiffData(null);
        setDiffPath('');
        setTimeout(() => { setApplying(false); setApplyStatus('idle'); }, 2000);
      } else {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error((err as { detail?: string }).detail || resp.statusText);
      }
    } catch (err) {
      setApplyStatus('error');
      setApplyMsg(err instanceof Error ? err.message : 'Failed to write file');
    }
  };

  return (
    <pre className="md-code-block">
      {lang && <div className="md-code-lang">{lang}</div>}
      <div className="md-code-actions">
        <button
          className={`md-copy-btn ${copied ? 'md-copy-btn--copied' : ''}`}
          onClick={handleCopy}
          title="Copy code"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
        <button
          className="md-apply-btn"
          onClick={startApply}
          title="Apply to file"
        >
          Apply
        </button>
        {isRunnable && (
          <button
            className="md-run-btn"
            onClick={handleRun}
            disabled={running}
            title="Run command"
          >
            {running ? '...' : 'Run'}
          </button>
        )}
      </div>
      {applying && !diffData && (
        <div className="md-apply-form">
          <input
            ref={inputRef}
            className="md-apply-input"
            type="text"
            placeholder="path/to/file.ts"
            value={applyPath}
            onChange={(e) => { setApplyPath(e.target.value); setApplyStatus('idle'); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitApply();
              if (e.key === 'Escape') cancelApply();
            }}
          />
          <button
            className="md-apply-submit"
            onClick={submitApply}
            disabled={applyStatus === 'saving'}
          >
            {applyStatus === 'saving' ? 'Diffing...' : 'Preview'}
          </button>
          <button className="md-apply-cancel" onClick={cancelApply}>Cancel</button>
          {applyMsg && (
            <span className={`md-apply-msg md-apply-msg--${applyStatus}`}>{applyMsg}</span>
          )}
        </div>
      )}
      {applying && diffData && (
        <DiffPreview diff={diffData.diff} path={diffPath} isNew={diffData.isNew} />
      )}
      {applying && diffData && (
        <div className="diff-actions">
          <button className="diff-cancel" onClick={() => { setDiffData(null); setDiffPath(''); }}>Cancel</button>
          <button
            className="diff-confirm"
            onClick={confirmApply}
            disabled={applyStatus === 'saving'}
          >
            {applyStatus === 'saving' ? 'Saving...' : `Write to ${diffPath}`}
          </button>
          {applyMsg && (
            <span className={`md-apply-msg md-apply-msg--${applyStatus}`}>{applyMsg}</span>
          )}
        </div>
      )}
      <code
        dangerouslySetInnerHTML={{ __html: highlightCode(code, lang) }}
      />
      {showOutput && runOutput && (
        <div className="md-run-output">
          <div className="md-run-output-header">
            <span className={`md-run-exit ${runOutput.exitCode === 0 ? 'md-run-exit--ok' : 'md-run-exit--err'}`}>
              exit: {runOutput.exitCode}
            </span>
            <button className="md-run-close" onClick={() => setShowOutput(false)}>×</button>
          </div>
          {runOutput.stdout && (
            <pre className="md-run-stdout">{runOutput.stdout.slice(-8000)}</pre>
          )}
          {runOutput.stderr && (
            <pre className="md-run-stderr">{runOutput.stderr.slice(-8000)}</pre>
          )}
          {!runOutput.stdout && !runOutput.stderr && (
            <p className="md-run-empty">(no output)</p>
          )}
        </div>
      )}
      {showOutput && !runOutput && running && (
        <div className="md-run-output">
          <p className="md-run-loading">Running...</p>
        </div>
      )}
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

    // Image syntax: ![alt](data:image/...)
    const imgMatch = processed.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    if (imgMatch) {
      result.push(
        <img
          key={`img-${i}`}
          src={imgMatch[2]}
          alt={imgMatch[1] || 'Image'}
          className="md-image"
          style={{ maxWidth: '100%', maxHeight: '400px', borderRadius: 'var(--radius-md)', margin: 'var(--space-2) 0', cursor: 'pointer' }}
          onClick={(e) => {
            const target = e.currentTarget;
            if (target.style.maxHeight === '400px') {
              target.style.maxHeight = 'none';
            } else {
              target.style.maxHeight = '400px';
            }
          }}
          loading="lazy"
        />,
      );
      continue;
    }

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
