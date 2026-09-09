// All QC reads share one response generation. A late request or retry must
// never replace the result of a newer correction, tab change or refresh.
export function createLatestLoader(read, { onStart, onData, onError, onFinish,
  wait = (ms) => new Promise(resolve => setTimeout(resolve, ms)) }) {
  let generation = 0
  let active = false
  return {
    invalidate() { generation++; active = false },
    async run(input, { quiet = false } = {}) {
      if (quiet && active) return
      const mine = ++generation
      const current = () => mine === generation
      active = true
      if (!quiet) onStart()
      try {
        for (let attempt = 0; attempt < 2 && current(); attempt++) {
          try {
            const result = await read(input)
            if (current()) onData(result)
            return
          } catch (error) {
            if (!current()) return
            if (attempt === 0 && !quiet && /fetch|network|load failed/i.test(String(error?.message || error))) {
              await wait(800)
              continue
            }
            if (!quiet) onError(error)
            return
          }
        }
      } finally {
        if (current()) {
          active = false
          if (!quiet) onFinish()
        }
      }
    },
  }
}

export async function readQualityTab(client, tab) {
  if (tab === 'records' || tab === 'records-poll') return {
    data: await client.get('/database/records'), resetShown: tab === 'records',
  }
  if (tab === 'changes' || tab === 'issues') {
    const path = tab === 'changes' ? '/database/changes?limit=300' : '/database/issues'
    const [value, data] = await Promise.all([client.get(path), client.get('/database/records')])
    return { [tab]: value, data }
  }
  if (tab === 'asks') return { asks: await client.get('/database/asks?limit=300') }
  if (tab === 'freshness-poll') return { freshness: await client.get('/database/freshness') }
  if (tab === 'freshness') {
    const [fresh, issues, uptime] = await Promise.allSettled([
      client.get('/database/freshness'), client.get('/database/issues'), client.get('/health/uptime'),
    ])
    if (fresh.status === 'rejected') throw fresh.reason
    return { freshness: fresh.value,
      ...(issues.status === 'fulfilled' ? { issues: issues.value } : {}),
      ...(uptime.status === 'fulfilled' ? { uptime: uptime.value } : {}),
    }
  }
  throw new Error('Unknown Quality Control tab')
}
