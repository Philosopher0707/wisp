export type DiffLineKind = 'add' | 'del' | 'ctx' | 'hunk' | 'meta';

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

export interface ParsedDiff {
  lines: DiffLine[];
  added: number;
  removed: number;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** Unified diff text -> typed lines with old/new line numbers. Header lines before the first hunk are `meta` and are not counted. */
export function parseUnifiedDiff(text: string): ParsedDiff {
  const lines: DiffLine[] = [];
  let added = 0;
  let removed = 0;
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;

  for (const raw of text.split('\n')) {
    const hunk = HUNK.exec(raw);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      inHunk = true;
      lines.push({ kind: 'hunk', text: raw, oldNo: null, newNo: null });
      continue;
    }
    if (!inHunk) {
      if (raw) lines.push({ kind: 'meta', text: raw, oldNo: null, newNo: null });
      continue;
    }
    if (raw.startsWith('+')) {
      added += 1;
      lines.push({ kind: 'add', text: raw.slice(1), oldNo: null, newNo: newNo++ });
    } else if (raw.startsWith('-')) {
      removed += 1;
      lines.push({ kind: 'del', text: raw.slice(1), oldNo: oldNo++, newNo: null });
    } else if (raw.startsWith('\\')) {
      lines.push({ kind: 'meta', text: raw, oldNo: null, newNo: null });
    } else if (raw !== '' || lines.length > 0) {
      lines.push({ kind: 'ctx', text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  // split('\n') leaves one empty trailing entry that the loop above renders as a context line.
  const last = lines[lines.length - 1];
  if (last && last.kind === 'ctx' && last.text === '' && text.endsWith('\n')) lines.pop();
  return { lines, added, removed };
}
