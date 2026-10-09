// hill's companion mod in the Claude sessions palace starts (claude.py's
// --plugin-dir): the work item above the prompt, a guard on git add -A /
// commit -a, and /select with the select tool in place of $PALACE_SELECT.
// palace's Python side stays authoritative: a broken mod loses only these
// extras, so no hook here refuses what it failed to judge.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import { sweeps } from './guard'
import { parseItem } from './parse'

const item = atom({ plugin: 'hill', key: 'item' } as const, null)
// A desk session's project (a key that's a folder, not an item).
const desk = atom({ plugin: 'hill', key: 'desk' } as const, null)

// palace's mark colours (dark theme), as in hill's app.py MARKS.
const COLOURS: Record<string, string> = {
  open: '#f783ac',
  ready: '#ff6b6b',
  waiting: '#ffa94d',
  running: '#ffd43b',
  doing: '#e9ecef',
  done: '#b197fc',
  dropped: '#868e96',
}

const SELECT_HELP =
  "Selects a note in palace's list, and its preview follows; Claude's pane stays on this session. " +
  'The path is relative to the project this session runs in, or absolute (work/014-x.md).'

// The work item's file palace named in $PALACE_CLAUDE_KEY, or '' for none,
// and the file a note's path written to selects it ($PALACE_SELECT).
let key = ''
let selectFile = ''

async function env($: EngineInterface, name: string): Promise<string> {
  const got = await $.process.run(['printenv', name])
  return got.exitCode === 0 ? got.stdout.trim() : ''
}

/** Read the item again and redraw the band; nothing for a desk session. */
async function refresh($: EngineInterface): Promise<void> {
  if (!key) return
  if (!key.endsWith('.md')) {
    await update($, desk, () => key.split('/').filter(Boolean).pop() ?? null)
    return
  }
  try {
    const text = await $.fs.read(key)
    await update($, item, () => parseItem(key, text))
  } catch {
    await update($, item, () => null)
  }
}

/** Tell palace to select `path`; what to say back. */
async function select($: EngineInterface, path: string): Promise<string> {
  const note = path.trim()
  if (!selectFile) return 'This session runs outside palace: there is no list to select in.'
  if (!note) return 'Name a note: /select work/014-x.md'
  await $.fs.write(selectFile, note + '\n')
  return `palace selects ${note}.`
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    // A mod has no environment of its own: palace's variables come from the process's.
    key = await env($, 'PALACE_CLAUDE_KEY')
    selectFile = await env($, 'PALACE_SELECT')
    await refresh($)
    // The item's file changes under the session (Pierre, a runner, a pull).
    if (key) $.clock.every(20_000, () => void refresh($))
    if (selectFile) {
      await $.command.register({ name: 'select', description: SELECT_HELP })
      await $.tool.register({
        name: 'select',
        description: SELECT_HELP + ' Use it for the work item you move on to, then stop.',
        inputSchema: {
          type: 'object',
          properties: { path: { type: 'string', description: "The note's path" } },
          required: ['path'],
        },
        isDeferred: false,
      })
    }
    return started
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    await refresh($)
    return done
  })

  on('command.run', { command: 'select' }, async ($, e) => ({ text: await select($, e.args) }))

  on('tool.call', { tool: 'mcp__hill__select' }, async ($, e) => {
    const path = (e as { path?: unknown }).path
    return { result: await select($, typeof path === 'string' ? path : '') }
  })

  on('tool.call', { tool: 'Bash' }, ($, e, next) => {
    const why = sweeps(e.command)
    return why ? { deny: why } : next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const shown = await read($, item)
    const project = await read($, desk)
    if (e.props.hasSurvey || (shown === null && project === null)) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    if (shown === null) {
      return (
        <Box>
          <Text dimColor>✦ {project} · desk session, on no work item</Text>
        </Box>
      )
    }
    return (
      <Box>
        <Text color={COLOURS[shown.status] ?? 'gray'}>● {shown.status} </Text>
        <Text dimColor>{shown.project} {shown.number} </Text>
        <Text>{shown.title}</Text>
        {shown.decisions > 0 ? <Text color={COLOURS.waiting}> ❓{shown.decisions}</Text> : null}
        {shown.next ? <Text dimColor wrap="truncate-end"> · next: {shown.next}</Text> : null}
      </Box>
    )
  })
}
