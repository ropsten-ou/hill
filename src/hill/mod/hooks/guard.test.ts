import { expect, test } from 'claude-code/testing'

import { sweeps } from './guard'

test('a git add or commit of everything is refused', () => {
  for (const line of [
    'git add -A',
    'git add --all',
    'git add .',
    'git add -u',
    'git -C ~/projects/hill add -A && git commit -m x',
    'cd hill; git commit -a -m "Work item 055"',
    'git commit -am "x"',
    'git commit --all',
    'GIT_EDITOR=true git commit -va',
  ]) expect(sweeps(line)).not.toBeNull()
})

test('staging by path, and words that only mention it, pass', () => {
  for (const line of [
    'git add work/055-companion-mod.md src/hill/claude.py',
    'git commit -m "Refuse git add -A and commit -a"',
    'git commit -ma',
    'git commit -F msg.txt',
    'echo "git add -A"',
    'git status && git diff',
    'git log --all',
    'git add -- ./src',
  ]) expect(sweeps(line)).toBeNull()
})

test('the session refuses the call, saying to stage by path', async $ => {
  const ran = await $.tool.call({ tool: 'Bash', command: 'git add -A && git commit -m x' })
  // A test's own $.tool.call reads the refusal as { deny }; the model, as an error result.
  expect(ran.deny).toContain('stage by path')
})
