import type { Item } from '../types'

const ITEM = /\/([^/]+)\/work\/(\d+)-[^/]+\.md$/

/** The item a palace session is on, from its file's path and text; null
 * for a key that isn't a work item (a project's desk session). */
export function parseItem(path: string, text: string): Item | null {
  const where = ITEM.exec(path)
  if (where === null || !text.startsWith('---\n')) return null
  const end = text.indexOf('\n---', 4)
  if (end < 0) return null
  const front = text.slice(4, end)
  const field = (name: string) => {
    const m = new RegExp(`^${name}:\\s*(.*)$`, 'm').exec(front)
    return m ? (m[1] ?? '').trim().replace(/^["']|["']$/g, '') : ''
  }
  const body = text.slice(end + 4)
  const title = /^# (.+)$/m.exec(body)?.[1]?.trim() ?? ''
  const before = /^## Before\n([\s\S]*?)(?=^## |$(?![\s\S]))/m.exec(body)?.[1] ?? ''
  const decisions = (before.match(/^[ \t]*[-*+][ \t]+\[ \][ \t]+Decide:/gm) ?? []).length
  return { project: where[1] ?? '', number: where[2] ?? '', title, status: field('status'), next: field('next'), decisions }
}
