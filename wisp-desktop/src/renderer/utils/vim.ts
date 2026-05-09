export type VimMode = 'normal' | 'insert' | 'visual' | 'visual-line';

export interface VimState {
  mode: VimMode;
  lastYanked: string;
}

export class VimEditor {
  mode: VimMode = 'normal';
  lastYanked: string = '';

  constructor(initialMode: VimMode = 'normal') {
    this.mode = initialMode;
  }

  getStatusLine(): string {
    switch (this.mode) {
      case 'normal': return '-- NORMAL --';
      case 'insert': return '-- INSERT --';
      case 'visual': return '-- VISUAL --';
      case 'visual-line': return '-- VISUAL LINE --';
    }
  }

  handleKeyDown(e: KeyboardEvent, textarea: HTMLTextAreaElement): boolean {
    if (this.mode === 'insert') {
      return this.handleInsert(e, textarea);
    }
    if (this.mode === 'normal') {
      return this.handleNormal(e, textarea);
    }
    if (this.mode === 'visual') {
      return this.handleVisual(e, textarea);
    }
    if (this.mode === 'visual-line') {
      return this.handleVisualLine(e, textarea);
    }
    return false;
  }

  private handleNormal(e: KeyboardEvent, textarea: HTMLTextAreaElement): boolean {
    const key = e.key;

    // Movement
    if (key === 'h') { e.preventDefault(); this.moveCursor(textarea, -1, 0); return true; }
    if (key === 'j') { e.preventDefault(); this.moveCursor(textarea, 0, 1); return true; }
    if (key === 'k') { e.preventDefault(); this.moveCursor(textarea, 0, -1); return true; }
    if (key === 'l') { e.preventDefault(); this.moveCursor(textarea, 1, 0); return true; }

    // Word jumps
    if (key === 'w') { e.preventDefault(); this.nextWord(textarea); return true; }
    if (key === 'b') { e.preventDefault(); this.prevWord(textarea); return true; }
    if (key === 'e') { e.preventDefault(); this.endWord(textarea); return true; }

    // Line bounds
    if (key === '0') { e.preventDefault(); this.goToLineStart(textarea); return true; }
    if (key === '$') { e.preventDefault(); this.goToLineEnd(textarea); return true; }
    if (key === '^') { e.preventDefault(); this.goToFirstNonWhitespace(textarea); return true; }

    // Document bounds
    if (key === 'g') {
      // Wait for second 'g'
      const handler = (e2: KeyboardEvent) => {
        textarea.removeEventListener('keydown', handler);
        if (e2.key === 'g') {
          e2.preventDefault();
          textarea.selectionStart = 0;
          textarea.selectionEnd = 0;
        }
      };
      textarea.addEventListener('keydown', handler, { once: true });
      e.preventDefault();
      return true;
    }
    if (key === 'G') {
      e.preventDefault();
      textarea.selectionStart = textarea.value.length;
      textarea.selectionEnd = textarea.value.length;
      return true;
    }

    // Enter insert mode
    if (key === 'i') { e.preventDefault(); this.mode = 'insert'; return true; }
    if (key === 'a') {
      e.preventDefault();
      this.mode = 'insert';
      const pos = textarea.selectionStart;
      if (pos < textarea.value.length) {
        textarea.selectionStart = pos + 1;
        textarea.selectionEnd = pos + 1;
      }
      return true;
    }
    if (key === 'o') {
      e.preventDefault();
      this.mode = 'insert';
      this.openLineBelow(textarea);
      return true;
    }
    if (key === 'O') {
      e.preventDefault();
      this.mode = 'insert';
      this.openLineAbove(textarea);
      return true;
    }
    if (key === 'I') { e.preventDefault(); this.goToFirstNonWhitespace(textarea); this.mode = 'insert'; return true; }
    if (key === 'A') { e.preventDefault(); this.goToLineEnd(textarea); this.mode = 'insert'; return true; }

    // Delete line
    if (key === 'd') {
      // Wait for second 'd'
      const handler = (e2: KeyboardEvent) => {
        textarea.removeEventListener('keydown', handler);
        if (e2.key === 'd') {
          e2.preventDefault();
          this.deleteLine(textarea);
        }
      };
      textarea.addEventListener('keydown', handler, { once: true });
      e.preventDefault();
      return true;
    }

    // Yank line
    if (key === 'y') {
      const handler = (e2: KeyboardEvent) => {
        textarea.removeEventListener('keydown', handler);
        if (e2.key === 'y') {
          e2.preventDefault();
          this.yankLine(textarea);
        }
      };
      textarea.addEventListener('keydown', handler, { once: true });
      e.preventDefault();
      return true;
    }

    // Paste
    if (key === 'p') {
      e.preventDefault();
      this.pasteAfter(textarea);
      return true;
    }
    if (key === 'P') {
      e.preventDefault();
      this.pasteBefore(textarea);
      return true;
    }

    // Delete character
    if (key === 'x') { e.preventDefault(); this.deleteChar(textarea); return true; }

    // Change
    if (key === 'c') {
      const handler = (e2: KeyboardEvent) => {
        textarea.removeEventListener('keydown', handler);
        if (e2.key === 'w') {
          e2.preventDefault();
          this.deleteWord(textarea);
          this.mode = 'insert';
        } else if (e2.key === 'c') {
          e2.preventDefault();
          this.deleteLine(textarea);
          this.mode = 'insert';
        }
      };
      textarea.addEventListener('keydown', handler, { once: true });
      e.preventDefault();
      return true;
    }

    return false;
  }

  private handleInsert(e: KeyboardEvent, _textarea: HTMLTextAreaElement): boolean {
    if (e.key === 'Escape') {
      e.preventDefault();
      this.mode = 'normal';
      return true;
    }
    // Ctrl+[ also exits insert mode
    if (e.key === '[' && e.ctrlKey) {
      e.preventDefault();
      this.mode = 'normal';
      return true;
    }
    return false;
  }

  private handleVisual(e: KeyboardEvent, textarea: HTMLTextAreaElement): boolean {
    if (e.key === 'Escape') {
      e.preventDefault();
      this.mode = 'normal';
      // Clear selection
      textarea.selectionEnd = textarea.selectionStart;
      return true;
    }
    // Movement keys extend selection
    if (['h', 'j', 'k', 'l', 'w', 'b', 'e', '0', '$', '^', 'G'].includes(e.key)) {
      // Let normal mode handle movement but keep visual mode
      this.handleNormal(e, textarea);
      // Extend selection instead of collapsing
      return true;
    }
    if (e.key === 'y') {
      e.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      this.lastYanked = textarea.value.substring(
        Math.min(start, end),
        Math.max(start, end),
      );
      this.mode = 'normal';
      textarea.selectionEnd = textarea.selectionStart;
      return true;
    }
    if (e.key === 'd') {
      e.preventDefault();
      const start = Math.min(textarea.selectionStart, textarea.selectionEnd);
      const end = Math.max(textarea.selectionStart, textarea.selectionEnd);
      this.lastYanked = textarea.value.substring(start, end);
      textarea.value = textarea.value.substring(0, start) + textarea.value.substring(end);
      textarea.selectionStart = start;
      textarea.selectionEnd = start;
      this.mode = 'normal';
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }
    return false;
  }

  private handleVisualLine(e: KeyboardEvent, textarea: HTMLTextAreaElement): boolean {
    if (e.key === 'Escape') {
      e.preventDefault();
      this.mode = 'normal';
      textarea.selectionEnd = textarea.selectionStart;
      return true;
    }
    if (['j', 'k', 'G'].includes(e.key)) {
      this.handleNormal(e, textarea);
      // Select full lines
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      this.selectFullLines(textarea, Math.min(start, end), Math.max(start, end));
      return true;
    }
    if (e.key === 'y') {
      e.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      this.lastYanked = textarea.value.substring(
        Math.min(start, end),
        Math.max(start, end),
      );
      this.mode = 'normal';
      textarea.selectionEnd = textarea.selectionStart;
      return true;
    }
    if (e.key === 'd') {
      e.preventDefault();
      const start = Math.min(textarea.selectionStart, textarea.selectionEnd);
      const end = Math.max(textarea.selectionStart, textarea.selectionEnd);
      this.lastYanked = textarea.value.substring(start, end);
      textarea.value = textarea.value.substring(0, start) + textarea.value.substring(end);
      textarea.selectionStart = start;
      textarea.selectionEnd = start;
      this.mode = 'normal';
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }
    return false;
  }

  // ── Helpers ──

  private getLineStart(value: string, pos: number): number {
    const before = value.lastIndexOf('\n', pos - 1);
    return before === -1 ? 0 : before + 1;
  }

  private getLineEnd(value: string, pos: number): number {
    const after = value.indexOf('\n', pos);
    return after === -1 ? value.length : after;
  }

  private moveCursor(textarea: HTMLTextAreaElement, dx: number, dy: number): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;

    if (dy !== 0) {
      // Get current column
      const lineStart = this.getLineStart(val, pos);
      const col = pos - lineStart;

      if (dy < 0) {
        // Move up
        const prevLineStart = this.getLineStart(val, lineStart - 1);
        const prevLineEnd = lineStart - 1;
        const prevLineLen = prevLineEnd - prevLineStart;
        const newCol = Math.min(col, prevLineLen);
        textarea.selectionStart = prevLineStart + newCol;
        textarea.selectionEnd = textarea.selectionStart;
      } else {
        // Move down
        const lineEnd = this.getLineEnd(val, pos);
        if (lineEnd < val.length) {
          const nextLineStart = lineEnd + 1;
          const nextLineEnd = this.getLineEnd(val, nextLineStart);
          const nextLineLen = nextLineEnd - nextLineStart;
          const newCol = Math.min(col, nextLineLen);
          // Don't move cursor past \n in next line
          const target = nextLineStart + newCol;
          textarea.selectionStart = Math.min(target, nextLineEnd);
          textarea.selectionEnd = textarea.selectionStart;
        }
      }
    } else if (dx !== 0) {
      const newPos = pos + dx;
      if (newPos >= 0 && newPos <= val.length) {
        textarea.selectionStart = newPos;
        textarea.selectionEnd = newPos;
      }
    }
  }

  private nextWord(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    let i = pos;

    // Skip current word
    while (i < val.length && /\w/.test(val[i])) i++;
    // Skip whitespace
    while (i < val.length && /\s/.test(val[i])) i++;

    textarea.selectionStart = i;
    textarea.selectionEnd = i;
  }

  private prevWord(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    let i = pos - 1;

    // Skip whitespace backwards
    while (i >= 0 && /\s/.test(val[i])) i--;
    // Skip word backwards
    while (i >= 0 && /\w/.test(val[i])) i--;

    textarea.selectionStart = i + 1;
    textarea.selectionEnd = i + 1;
  }

  private endWord(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    let i = pos;

    // If at end of word, go to next word's end
    if (i < val.length && /\w/.test(val[i])) {
      while (i < val.length && /\w/.test(val[i])) i++;
    } else {
      while (i < val.length && /\s/.test(val[i])) i++;
      while (i < val.length && /\w/.test(val[i])) i++;
    }

    textarea.selectionStart = Math.max(0, i - 1);
    textarea.selectionEnd = textarea.selectionStart;
  }

  private goToLineStart(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const lineStart = this.getLineStart(val, textarea.selectionStart);
    textarea.selectionStart = lineStart;
    textarea.selectionEnd = lineStart;
  }

  private goToLineEnd(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const lineEnd = this.getLineEnd(val, textarea.selectionStart);
    textarea.selectionStart = lineEnd;
    textarea.selectionEnd = lineEnd;
  }

  private goToFirstNonWhitespace(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const lineStart = this.getLineStart(val, textarea.selectionStart);
    const lineEnd = this.getLineEnd(val, textarea.selectionStart);
    let i = lineStart;
    while (i < lineEnd && /\s/.test(val[i])) i++;
    textarea.selectionStart = i;
    textarea.selectionEnd = i;
  }

  private deleteLine(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    const lineStart = this.getLineStart(val, pos);
    const lineEnd = this.getLineEnd(val, pos);

    // Include trailing newline if not last line
    const end = lineEnd < val.length ? lineEnd + 1 : lineEnd;
    this.lastYanked = val.substring(lineStart, end);
    textarea.value = val.substring(0, lineStart) + val.substring(end);
    textarea.selectionStart = lineStart;
    textarea.selectionEnd = lineStart;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  private yankLine(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    const lineStart = this.getLineStart(val, pos);
    const lineEnd = this.getLineEnd(val, pos);
    this.lastYanked = val.substring(lineStart, lineEnd) + '\n';
  }

  private deleteChar(textarea: HTMLTextAreaElement): void {
    const pos = textarea.selectionStart;
    if (pos < textarea.value.length) {
      this.lastYanked = textarea.value[pos];
      textarea.value = textarea.value.substring(0, pos) + textarea.value.substring(pos + 1);
      textarea.selectionStart = pos;
      textarea.selectionEnd = pos;
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  private deleteWord(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    let i = pos;
    while (i < val.length && /\w/.test(val[i])) i++;
    this.lastYanked = val.substring(pos, i);
    textarea.value = val.substring(0, pos) + val.substring(i);
    textarea.selectionStart = pos;
    textarea.selectionEnd = pos;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  private pasteAfter(textarea: HTMLTextAreaElement): void {
    if (!this.lastYanked) return;
    const pos = textarea.selectionStart;

    // Move past current character if on a line
    let insertPos = pos;
    if (insertPos < textarea.value.length && textarea.value[insertPos] !== '\n') {
      insertPos++;
    } else if (insertPos < textarea.value.length) {
      insertPos++;
    }

    textarea.value = textarea.value.substring(0, insertPos) + this.lastYanked + textarea.value.substring(insertPos);
    textarea.selectionStart = insertPos + this.lastYanked.length;
    textarea.selectionEnd = textarea.selectionStart;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  private pasteBefore(textarea: HTMLTextAreaElement): void {
    if (!this.lastYanked) return;
    const pos = textarea.selectionStart;
    textarea.value = textarea.value.substring(0, pos) + this.lastYanked + textarea.value.substring(pos);
    textarea.selectionStart = pos;
    textarea.selectionEnd = pos;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  private openLineBelow(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    const lineEnd = this.getLineEnd(val, pos);
    textarea.value = val.substring(0, lineEnd) + '\n' + val.substring(lineEnd);
    textarea.selectionStart = lineEnd + 1;
    textarea.selectionEnd = lineEnd + 1;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  private openLineAbove(textarea: HTMLTextAreaElement): void {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    const lineStart = this.getLineStart(val, pos);
    textarea.value = val.substring(0, lineStart) + '\n' + val.substring(lineStart);
    textarea.selectionStart = lineStart;
    textarea.selectionEnd = lineStart;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  private selectFullLines(textarea: HTMLTextAreaElement, from: number, to: number): void {
    const val = textarea.value;
    const startLineStart = this.getLineStart(val, from);
    let endLineEnd = this.getLineEnd(val, to);
    // Include trailing newline if selecting past it
    if (endLineEnd < val.length) endLineEnd++;
    textarea.selectionStart = startLineStart;
    textarea.selectionEnd = endLineEnd;
  }
}
