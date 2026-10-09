import { expect, test } from 'claude-code/testing'

import { parseItem } from './parse'

const ITEM = `---
status: waiting
since: 2026-10-08
next: "Once Pierre decides: park sessions"
---
# A session parks when its item hands off

## Goal
x

## Before
- [ ] Decide: when does waiting park?
- [x] Decided 2026-10-08: something (Pierre)
- [ ] Decide: desk sessions?

## Notes
- [ ] Decide: not in Before, not counted
`

test('an item gives its project, number, status, next, title and open decisions', () => {
  expect(parseItem('/Users/p/projects/hill/work/048-park.md', ITEM)).toEqual({
    project: 'hill', number: '048', title: 'A session parks when its item hands off',
    status: 'waiting', next: 'Once Pierre decides: park sessions', decisions: 2,
  })
})

test('a project key is no item', () => {
  expect(parseItem('/Users/p/projects/hill', ITEM)).toBeNull()
})
