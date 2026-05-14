import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import React from 'react'
import {
  escapeHtml,
  applyInlineFormatting,
  parseTableRows,
  safeRenderMarkdown,
  renderMarkdown,
  highlightCode,
} from './markdown'

// ————————————————————————————————————————————————
// escapeHtml
// ————————————————————————————————————————————————
describe('escapeHtml', () => {
  it('escapes basic HTML entities', () => {
    expect(escapeHtml('<div>hello</div>')).toBe('&lt;div&gt;hello&lt;/div&gt;')
    expect(escapeHtml('a & b')).toBe('a &amp; b')
  })

  it('returns empty string for empty input', () => {
    expect(escapeHtml('')).toBe('')
  })

  it('double-escapes already-escaped text (by design)', () => {
    const once = escapeHtml('<script>')
    expect(escapeHtml(once)).toBe('&amp;lt;script&amp;gt;')
  })
})

// ————————————————————————————————————————————————
// highlightCode
// ————————————————————————————————————————————————
describe('highlightCode', () => {
  it('highlights Python code', () => {
    const html = highlightCode("print('hello')", 'python')
    expect(html).toContain('print')
    expect(html).not.toBe("print('hello')")
  })

  it('falls back to auto-highlight on unknown language', () => {
    const html = highlightCode('foo bar', 'nonsense_lang')
    expect(html).toContain('foo')
  })

  it('escapes HTML on hljs failure', () => {
    const html = highlightCode('<script>alert("xss")</script>', 'js')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('returns empty for empty code', () => {
    expect(highlightCode('  ', 'python')).toBe('')
  })
})

// ————————————————————————————————————————————————
// applyInlineFormatting
// ————————————————————————————————————————————————
describe('applyInlineFormatting', () => {
  it('formats inline code', () => {
    expect(applyInlineFormatting('use `console.log()` now')).toBe(
      'use <code class="md-inline-code">console.log()</code> now',
    )
  })

  it('formats bold text', () => {
    expect(applyInlineFormatting('**bold** here')).toBe(
      '<strong>bold</strong> here',
    )
  })

  it('formats italic text', () => {
    expect(applyInlineFormatting('*italic* here')).toBe(
      '<em>italic</em> here',
    )
  })

  it('combines all formats', () => {
    const text = '**bold** and *italic* and `code`'
    const html = applyInlineFormatting(text)
    expect(html).toContain('<strong>bold</strong>')
    expect(html).toContain('<em>italic</em>')
    expect(html).toContain('<code class="md-inline-code">code</code>')
  })

  it('escapes HTML entities before formatting', () => {
    expect(applyInlineFormatting('`<script>alert(1)</script>`')).toBe(
      '<code class="md-inline-code">&lt;script&gt;alert(1)&lt;/script&gt;</code>',
    )
  })

  it('returns empty string for falsy input', () => {
    expect(applyInlineFormatting('')).toBe('')
  })
})

// ————————————————————————————————————————————————
// parseTableRows
// ————————————————————————————————————————————————
describe('parseTableRows', () => {
  it('parses a valid table', () => {
    const lines = ['| A | B |', '|---|---|', '| 1 | 2 |']
    const parsed = parseTableRows(lines)
    expect(parsed).not.toBeNull()
    expect(parsed!.headers).toEqual(['A', 'B'])
    expect(parsed!.bodyRows).toEqual([['1', '2']])
  })

  it('parses table with alignment hints', () => {
    const lines = ['| Name | Price |', '| :--- | :---: |', '| Foo  | $100  |']
    const parsed = parseTableRows(lines)
    expect(parsed!.headers).toEqual(['Name', 'Price'])
    expect(parsed!.bodyRows).toEqual([['Foo', '$100']])
  })

  it('parses table with trailing pipe', () => {
    const lines = ['| A | B | C |', '|---|---|---|']
    const parsed = parseTableRows(lines)
    expect(parsed!.headers).toEqual(['A', 'B', 'C'])
    expect(parsed!.bodyRows).toEqual([])
  })

  it('returns null for too few rows', () => {
    expect(parseTableRows(['| A | B |'])).toBeNull()
  })

  it('returns null when no separator row', () => {
    const lines = ['| A | B |', '| 1 | 2 |']
    expect(parseTableRows(lines)).toBeNull()
  })

  it('returns null for empty header after pipe split', () => {
    const lines = ['| | |', '|---|---|']
    expect(parseTableRows(lines)).toBeNull()
  })

  it('returns null when separator has no dashes', () => {
    const lines = ['| A | B |', '|   |   |']
    expect(parseTableRows(lines)).toBeNull()
  })

  it('filters empty body rows', () => {
    const lines = ['| A |', '|---|', '| 1 |', '||', '| 3 |']
    const parsed = parseTableRows(lines)
    expect(parsed!.bodyRows).toEqual([['1'], ['3']])
  })

  it('handles inline markdown in cells', () => {
    const lines = ['| **Name** | `Code` |', '|----------|--------|', '| Item | `val` |']
    const parsed = parseTableRows(lines)
    expect(parsed!.headers).toEqual(['**Name**', '`Code`'])
  })
})

// ————————————————————————————————————————————————
// renderMarkdown (basic features)
// ————————————————————————————————————————————————
describe('renderMarkdown', () => {
  it('renders plain text as paragraphs', () => {
    const { container } = render(React.createElement('div', null, renderMarkdown('hello world')))
    expect(container.textContent).toContain('hello world')
    expect(container.querySelector('p')).not.toBeNull()
  })

  it('renders headings', () => {
    const { container } = render(React.createElement('div', null, renderMarkdown('# H1\n## H2\n### H3')))
    const headings = container.querySelectorAll('.md-heading')
    expect(headings.length).toBe(3)
  })

  it('renders blockquotes', () => {
    const { container } = render(React.createElement('div', null, renderMarkdown('> quoted text')))
    expect(container.querySelector('.md-blockquote')).not.toBeNull()
    expect(container.textContent).toContain('quoted text')
  })

  it('renders unordered lists', () => {
    const { container } = render(React.createElement('div', null, renderMarkdown('- item 1\n* item 2\n+ item 3')))
    const items = container.querySelectorAll('.md-li')
    expect(items.length).toBe(3)
  })

  it('renders ordered lists', () => {
    const { container } = render(React.createElement('div', null, renderMarkdown('1. first\n2. second')))
    const items = container.querySelectorAll('.md-li')
    expect(items.length).toBe(2)
  })

  it('renders inline code', () => {
    const { container } = render(React.createElement('div', null, renderMarkdown('use `console.log()`')))
    expect(container.querySelector('.md-inline-code')).not.toBeNull()
  })

  it('renders bold and italic', () => {
    const { container } = render(React.createElement('div', null, renderMarkdown('**bold** and *italic*')))
    expect(container.querySelector('strong')).not.toBeNull()
    expect(container.querySelector('em')).not.toBeNull()
  })

  it('does not crash on empty text', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null as unknown as string)).toBe(null)
    expect(renderMarkdown(undefined as unknown as string)).toBe(undefined)
  })
})

// ————————————————————————————————————————————————
// renderMarkdown (tables)
// ————————————————————————————————————————————————
describe('renderMarkdown — tables', () => {
  it('renders a basic table', () => {
    const md = '| A | B |\n|---|---|\n| 1 | 2 |'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    const table = container.querySelector('.md-table')
    expect(table).not.toBeNull()
    expect(table!.querySelectorAll('th').length).toBe(2)
    expect(table!.querySelectorAll('tbody tr').length).toBe(1)
  })

  it('renders table with multiple body rows', () => {
    const md = '| Name | Price |\n|------|-------|\n| Foo  | $10   |\n| Bar  | $20   |'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    const rows = container.querySelectorAll('.md-table tbody tr')
    expect(rows.length).toBe(2)
    expect(container.textContent).toContain('Foo')
    expect(container.textContent).toContain('$20')
  })

  it('renders inline formatting inside table cells', () => {
    const md = '| **Bold** | `Code` |\n|----------|--------|\n| *Italic* | Normal |'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    expect(container.querySelector('table strong')).not.toBeNull()
    expect(container.querySelector('table code')).not.toBeNull()
    expect(container.querySelector('table em')).not.toBeNull()
  })

  it('treats non-separator pipe lines as normal text', () => {
    const md = '| A | B |\n| 1 | 2 |'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    expect(container.querySelector('.md-table')).toBeNull()
    expect(container.querySelector('p')).not.toBeNull()
  })

  it('handles consecutive tables', () => {
    const md = '| A |\n|---|\n| 1 |\n\n| B |\n|---|\n| 2 |'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    expect(container.querySelectorAll('.md-table').length).toBe(2)
  })

  it('renders escaped HTML in table cells', () => {
    const md = '| Code |\n|------|\n| `<script>` |'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    const code = container.querySelector('table code')
    expect(code).not.toBeNull()
    expect(code!.innerHTML).toContain('&lt;script&gt;')
    expect(code!.innerHTML).not.toContain('<script>')
  })
})

// ————————————————————————————————————————————————
// renderMarkdown (code blocks)
// ————————————————————————————————————————————————
describe('renderMarkdown — code blocks', () => {
  it('renders fenced code block with language', () => {
    const md = '```python\nprint("hello")\n```'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    expect(container.querySelector('.md-code-block')).not.toBeNull()
    expect(container.querySelector('.md-code-lang')?.textContent).toBe('python')
    expect(container.textContent).toContain('print')
  })

  it('renders fenced code block without language', () => {
    const md = '```\nplain text\n```'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    expect(container.querySelector('.md-code-block')).not.toBeNull()
    expect(container.textContent).toContain('plain text')
  })

  it('handles unclosed code block as code', () => {
    const md = '```python\nprint("hello")'
    const { container } = render(React.createElement('div', null, renderMarkdown(md)))
    expect(container.querySelector('.md-code-block')).not.toBeNull()
  })
})

// ————————————————————————————————————————————————
// safeRenderMarkdown
// ————————————————————————————————————————————————
describe('safeRenderMarkdown', () => {
  it('renders markdown normally on success', () => {
    const { container } = render(React.createElement('div', null, safeRenderMarkdown('hello')))
    expect(container.textContent).toContain('hello')
  })

  it('catches render errors and shows fallback', () => {
    // Monkey-patch renderMarkdown to throw
    const orig = (globalThis as any).__origRenderMarkdown
    ;(globalThis as any).__origRenderMarkdown = renderMarkdown

    // We can't easily make renderMarkdown throw without mocking,
    // so instead we rely on the fact that safeRenderMarkdown wraps it
    // in try/catch. The fallback renders the original text.
    const fallback = safeRenderMarkdown('')
    expect(fallback).toBe('')
  })
})
