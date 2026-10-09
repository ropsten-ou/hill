export type Item = {
  project: string
  number: string
  title: string
  status: string
  next: string
  decisions: number
}

declare module 'claude-code' {
  interface PluginState {
    'hill': { item: Item | null; desk: string | null }
  }
}
