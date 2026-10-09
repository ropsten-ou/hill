// A git command that stages everything sweeps up changes that aren't the
// session's: on 2026-10-07 a `git commit -a` took Pierre's `session:` line
// into a commit with the wrong message. So stage by path.

export const STAGE_BY_PATH =
  'hill: stage by path (git add <file>…, then git commit), not with git add -A / . / -u or git commit -a: ' +
  "those sweep up changes that aren't this session's, such as a session: line or another item's edits."

// Short options of git commit that take a value, which ends a cluster (`-ma` is the message "a").
const COMMIT_VALUED = new Set(['m', 'F', 'C', 'c', 't'])
// Options of git itself that take a value as the next word.
const GIT_VALUED = new Set(['-C', '-c'])

/** The words of a shell command, split into its simple commands at ;, &&,
 * ||, |, & and newlines, with quotes and backslashes taken off. */
export function commands(line: string): string[][] {
  const all: string[][] = []
  let words: string[] = []
  let word: string | null = null
  const end = () => {
    if (word !== null) words.push(word)
    word = null
  }
  const split = () => {
    end()
    if (words.length) all.push(words)
    words = []
  }
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (c === "'") {
      const close = line.indexOf("'", i + 1)
      const to = close < 0 ? line.length : close
      word = (word ?? '') + line.slice(i + 1, to)
      i = to
    } else if (c === '"') {
      word = word ?? ''
      for (i++; i < line.length && line[i] !== '"'; i++) {
        if (line[i] === '\\' && i + 1 < line.length) i++
        word += line[i]
      }
    } else if (c === '\\' && i + 1 < line.length) {
      word = (word ?? '') + line[++i]
    } else if (c === ';' || c === '|' || c === '&' || c === '\n' || c === '(' || c === ')') {
      split()
    } else if (c === ' ' || c === '\t') {
      end()
    } else {
      word = (word ?? '') + c
    }
  }
  split()
  return all
}

/** Whether one simple command is a git add or commit that stages everything. */
function sweepsOne(words: string[]): boolean {
  const word = (n: number) => words[n] ?? ''
  let i = 0
  while (/^[A-Za-z_][A-Za-z0-9_]*=/.test(word(i))) i++  // VAR=x git …
  if (!/(^|\/)git$/.test(word(i))) return false
  for (i++; word(i).startsWith('-'); i++) {
    if (GIT_VALUED.has(word(i))) i++
  }
  const sub = words[i]
  const rest = words.slice(i + 1)
  if (sub === 'add') {
    let options = true
    return rest.some(w => {
      if (w === '--') {
        options = false
        return false
      }
      if (options && (w === '--all' || w === '--update')) return true
      if (options && /^-[A-Za-z]+$/.test(w)) return /[Au]/.test(w)
      return w === '.' || w === ':/' || w === ':'
    })
  }
  if (sub === 'commit') {
    for (const w of rest) {
      if (w === '--') break
      if (w === '--all') return true
      if (!/^-[A-Za-z]/.test(w) || w.startsWith('--')) continue
      for (const c of w.slice(1)) {
        if (c === 'a') return true
        if (COMMIT_VALUED.has(c)) break
      }
    }
  }
  return false
}

/** Why `line`, a Bash command, is refused, or null for one that may run. */
export function sweeps(line: string): string | null {
  return commands(line).some(sweepsOne) ? STAGE_BY_PATH : null
}
