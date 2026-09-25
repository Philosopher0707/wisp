import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import * as ts from 'typescript'

/**
 * Single-authority guard for the client's `Authorization` header.
 *
 * The defect that motivated this: `getCheckpointDiff` builds its own `fetch`
 * call (it reads a plain-text diff, so it cannot use `apiFetch`, which parses
 * JSON) and it called the header builder with **no argument** — so the
 * checkpoint-diff request shipped unauthenticated while every other request was
 * authenticated.
 *
 * Nothing could have caught it, and the reason is the shape of the duplication:
 * the renderer constructs the header in **27 places across 18 files**. The
 * canonical builder is one of those 27, so the question "does this module build
 * the header?" answers *yes* for every module — including the broken one. The
 * invariant is therefore not "the header is present". It is:
 *
 *   **there is exactly one place that knows how to build it.**
 *
 * This test ratchets the duplication. It fails when a *new* file starts
 * building the header, and it also fails when a recorded one *stops* — so this
 * list cannot quietly decay the way the 2026-08-24 audit's inventory did
 * (`docs/audit-2026-08-24.md:270`).
 *
 * Scope note: `src/main/backend.ts` is deliberately NOT covered. It is the
 * Electron main process — a separate bundle from the renderer, so it cannot
 * import `makeAuthHeaders` at all. Its single construction site is pinned
 * separately below so it cannot silently multiply either.
 */

const CANONICAL = 'src/renderer/hooks/useApi.ts'

/** The main process cannot share the renderer helper; its site is pinned as-is. */
const MAIN_PROCESS_SITES = 1

/**
 * Measured 2026-09-21 by AST scan. Each entry is a re-implementation of the
 * header that should eventually route through `makeAuthHeaders`. The value is
 * the number of construction sites in that file, not the number of files.
 */
const KNOWN_RENDERER_DUPLICATES: Record<string, number> = {
  'src/renderer/App.tsx': 3,
  'src/renderer/components/ArenaPanel.tsx': 2,
  'src/renderer/components/CheckpointPanel.tsx': 3,
  'src/renderer/components/QuickFileModal.tsx': 1,
  'src/renderer/components/SearchModal.tsx': 1,
  'src/renderer/components/chat/BackgroundAgentBanner.tsx': 2,
  'src/renderer/components/chat/ChatArea.tsx': 1,
  'src/renderer/components/chat/CompletionGhost.tsx': 1,
  'src/renderer/components/chat/InlineEdit.tsx': 2,
  'src/renderer/components/chat/MentionPopup.tsx': 1,
  'src/renderer/components/chat/MessageBubble.tsx': 1,
  'src/renderer/components/chat/ProjectContextBar.tsx': 1,
  'src/renderer/components/files/FileExplorer.tsx': 1,
  'src/renderer/components/topbar/TopBar.tsx': 1,
  'src/renderer/hooks/useMenuIPC.ts': 1,
  'src/renderer/hooks/useWebSocket.ts': 1,
  'src/renderer/utils/markdown.tsx': 3,
}

/** A source file that constructs an `Authorization` header, and where. */
interface HeaderSite {
  file: string
  lines: number[]
}

/**
 * Enumerate header construction sites with the TypeScript AST.
 *
 * AST rather than a text scan on purpose: comments are not nodes, so the prose
 * in this file (and the many explanatory comments in the client) cannot
 * register as a construction site. Test files are excluded — they assert about
 * the header, they do not build it.
 */
function findHeaderSites(root: string): HeaderSite[] {
  const files: string[] = []
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry)
      if (statSync(path).isDirectory()) walk(path)
      else if (/\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path)) files.push(path)
    }
  }
  walk(join(root, 'src'))

  const sites: HeaderSite[] = []
  for (const path of files) {
    const source = ts.createSourceFile(
      path,
      readFileSync(path, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    )
    const lines: number[] = []
    const visit = (node: ts.Node): void => {
      let at: ts.Node | null = null
      // `headers['Authorization'] = ...`
      if (ts.isStringLiteral(node) && node.text === 'Authorization') at = node
      // `{ Authorization: ... }`
      else if (
        (ts.isPropertyAssignment(node) || ts.isPropertySignature(node)) &&
        node.name.getText(source) === 'Authorization'
      )
        at = node.name
      if (at) lines.push(source.getLineAndCharacterOfPosition(at.getStart(source)).line + 1)
      ts.forEachChild(node, visit)
    }
    visit(source)
    if (lines.length) sites.push({ file: relative(root, path), lines: lines.sort((a, b) => a - b) })
  }
  return sites.sort((a, b) => a.file.localeCompare(b.file))
}

const root = process.cwd()
const allSites = findHeaderSites(root)
const rendererSites = allSites.filter((s) => s.file.startsWith('src/renderer/'))
const mainSites = allSites.filter((s) => !s.file.startsWith('src/renderer/'))

const countOf = (sites: HeaderSite[], file: string): number =>
  sites.find((s) => s.file === file)?.lines.length ?? 0

describe('Authorization header — single authority', () => {
  it('constructs the header in exactly one canonical place', () => {
    const canonical = allSites.find((s) => s.file === CANONICAL)

    // Presence assertion: the canonical builder must actually be found, or the
    // assertions below would pass vacuously against an empty scan.
    expect(canonical, `${CANONICAL} no longer constructs the header`).toBeDefined()
    expect(canonical?.lines).toHaveLength(1)
  })

  it('pins the main-process site, which cannot share the renderer helper', () => {
    expect(mainSites.flatMap((s) => s.lines)).toHaveLength(MAIN_PROCESS_SITES)
  })

  it('ratchets the known renderer re-implementations', () => {
    const actual: Record<string, number> = {}
    for (const site of rendererSites) {
      if (site.file === CANONICAL) continue
      actual[site.file] = site.lines.length
    }

    const added = Object.keys(actual).filter((f) => !(f in KNOWN_RENDERER_DUPLICATES))
    const removed = Object.keys(KNOWN_RENDERER_DUPLICATES).filter((f) => !(f in actual))
    const changed = Object.keys(actual).filter(
      (f) => f in KNOWN_RENDERER_DUPLICATES && actual[f] !== KNOWN_RENDERER_DUPLICATES[f],
    )

    const problems: string[] = []
    if (added.length) {
      problems.push(
        `NEW header construction site(s) — route them through makeAuthHeaders, or ` +
          `record them here deliberately: ${added.join(', ')}`,
      )
    }
    if (removed.length) {
      problems.push(
        `recorded site(s) are gone — good, but the record must not drift; ` +
          `delete them from KNOWN_RENDERER_DUPLICATES: ${removed.join(', ')}`,
      )
    }
    for (const f of changed) {
      problems.push(
        `${f}: ${KNOWN_RENDERER_DUPLICATES[f]} site(s) recorded, ${actual[f]} found`,
      )
    }

    expect(problems.join('\n')).toBe('')
  })

  it('does not leave the canonical builder bypassed by a raw fetch to the API', () => {
    // A file may call `fetch()` on `/api/...` only if it builds the header.
    // This is the specific shape of the defect: a raw fetch that forgot to.
    const offenders: string[] = []
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir)) {
        const path = join(dir, entry)
        if (statSync(path).isDirectory()) {
          walk(path)
          continue
        }
        if (!/\.tsx?$/.test(path) || /\.test\.tsx?$/.test(path)) continue
        const rel = relative(root, path)
        if (!rel.startsWith('src/renderer/')) continue
        const source = readFileSync(path, 'utf8')
        if (!/fetch\(/.test(source)) continue
        if (!/\/api\//.test(source)) continue
        const builds = rendererSites.some((s) => s.file === rel)
        if (!builds) offenders.push(rel)
      }
    }
    walk(join(root, 'src'))

    expect(
      offenders,
      `these renderer modules call the API but never build an Authorization header`,
    ).toEqual([])
  })
})
