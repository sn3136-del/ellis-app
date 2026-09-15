// All QC reads share one response generation. A late request or retry must
// never replace the result of a newer correction, tab change or refresh.
export function createLatestLoader(read, { onStart, onData, onError, onFinish,
  wait = (ms) => new Promise(resolve => setTimeout(resolve, ms)) }) {
  let generation = 0
  let active = false
  let controller = null
  return {
    invalidate() { generation++; active = false; controller?.abort() },
    async run(input, { quiet = false } = {}) {
      if (quiet && active) return { status: 'skipped' }
      const mine = ++generation
      controller?.abort()
      const requestController = new AbortController()
      controller = requestController
      const current = () => mine === generation
      active = true
      if (!quiet) onStart()
      try {
        for (let attempt = 0; attempt < 2 && current(); attempt++) {
          try {
            const result = await read(input, { signal: requestController.signal })
            if (current()) {
              onData(result)
              return { status: 'loaded' }
            }
            return { status: 'superseded' }
          } catch (error) {
            if (!current()) return { status: 'superseded' }
            if (attempt === 0 && !quiet && /fetch|network|load failed/i.test(String(error?.message || error))) {
              await wait(800)
              continue
            }
            if (!quiet) onError(error)
            return { status: 'failed' }
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

export async function refreshQualityRecord(client, route, loader, currentTab) {
  const activeLoader = () => typeof loader === 'function' ? loader() : loader
  activeLoader().invalidate()
  const response = await client.post('/database/routes/research', route)
  // Resolve the visible tab after the mutation: an old Records closure must
  // not replace a Freshness view the operator selected while it was running.
  const reloaded = await activeLoader().run(typeof currentTab === 'function' ? currentTab() : currentTab)
  if (reloaded?.status === 'superseded') return { ...response, quality_reload: 'superseded' }
  if (reloaded?.status !== 'loaded') {
    // The source operation may have committed. Do not repeat it automatically
    // or report a successful display refresh while the old list remains.
    const error = new Error('The source check returned, but the Quality Control list did not reload.')
    error.code = 'quality_reload_failed'
    throw error
  }
  return response
}

export async function publishQualityRecord(client, route, loader, currentTab) {
  const activeLoader = () => typeof loader === 'function' ? loader() : loader
  activeLoader().invalidate()
  let response, failure
  try {
    // A recorded approval is not proof that the traveler answer was released.
    // Never retry this write automatically, including after a network error.
    response = await client.databaseApprove(route)
    if (response?.published !== true || response?.held !== false) {
      failure = new Error('Publication was not confirmed. Check the current record before trying again.')
      failure.code = 'publication_unconfirmed'
    }
  } catch (error) { failure = error }
  const reloaded = await activeLoader().run(typeof currentTab === 'function' ? currentTab() : currentTab)
  if (failure) throw failure
  if (!['loaded', 'superseded'].includes(reloaded?.status)) {
    const error = new Error('The answer was published, but the Quality Control list did not reload. Reload the page to see its current status.')
    error.code = 'publication_reload_failed'
    throw error
  }
  return { ...response, quality_reload: reloaded.status }
}

export function qualityRefreshOutcome(research) {
  const changed = Array.isArray(research?.changed) ? research.changed : []
  const disputed = Array.isArray(research?.disputed_fields) ? research.disputed_fields : []
  if (disputed.length) return { kind: changed.length ? 'correctedDisputed' : 'disputed', tone: 'warning', changed }
  if (changed.length) return { kind: research.renewed === true ? 'corrected' : 'correctedPartial', tone: research.renewed === true ? 'success' : 'warning', changed }
  if (research?.outcome === 'checked' && research.renewed === true) return { kind: 'ok', tone: 'success', changed }
  if (research?.provider_unavailable || research?.outcome === 'provider_error') return { kind: 'providerUnavailable', tone: 'warning', changed }
  if (research?.outcome === 'checked' || research?.source_reads > 0) return { kind: 'partial', tone: 'warning', changed }
  return { kind: 'noRead', tone: 'warning', changed }
}

export async function readQualityTab(client, tab, options = {}) {
  const get = path => client.get(path, options)
  if (tab === 'records' || tab === 'records-poll') return {
    data: await get('/database/records'), resetShown: tab === 'records',
  }
  if (tab === 'changes') return { changes: await get('/database/changes?limit=300') }
  if (tab === 'issues') {
    // Current canonical records are required for the side-by-side dispute
    // comparison. Other tabs must not wait for this unrelated large dataset.
    const [issues, data] = await Promise.all([get('/database/issues'), get('/database/records')])
    return { issues, data }
  }
  if (tab === 'asks') return { asks: await get('/database/asks?limit=300') }
  if (tab === 'freshness' || tab === 'freshness-poll') return {
    freshness: await get('/database/freshness?schedule_only=true'),
  }
  throw new Error('Unknown Quality Control tab')
}
