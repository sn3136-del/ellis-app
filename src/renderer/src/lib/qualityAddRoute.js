import { qualityRefreshOutcome } from './qualityLoader.js'

// Share a pending submission, including the tiny interval before React paints
// the disabled button. Never replay an ambiguous POST or a saved manual edit.
export function createQualityRouteAdder({ client, loader, currentTab, isActive = () => true, onStored = () => {} }) {
  let pending = null
  return function add(route, manual = null) {
    if (pending) return pending
    const activeLoader = () => typeof loader === 'function' ? loader() : loader
    activeLoader().invalidate()
    const operation = async () => {
      let response, failure, fieldsSaved = false
      try {
        if (manual) {
          await client.post('/database/records/edit', { ...route, ...manual })
          fieldsSaved = true
          response = await client.databaseLookup(route)
        } else response = await client.post('/database/routes/research', route)
        if (response?.record_present !== true) {
          failure = new Error('The route was not confirmed in the database. Reload the list before trying again.')
          failure.code = 'add_unconfirmed'
        }
      } catch (error) { failure = error }
      if (failure && fieldsSaved) {
        failure = Object.assign(new Error('Your sourced fields were saved, but the route answer was not confirmed. Reload the list before retrying.'),
          { code: 'add_manual_partial', cause: failure })
      }
      if (!isActive()) {
        if (failure) throw failure
        return { ...response, quality_reload: 'superseded' }
      }
      const tab = typeof currentTab === 'function' ? currentTab() : currentTab
      if (!failure) onStored(response, route, tab)
      const reloaded = await activeLoader().run(tab)
      if (failure) throw failure
      if (!['loaded', 'superseded'].includes(reloaded?.status)) {
        const error = new Error('The route was stored, but the list did not reload. Reload the list; do not add it again.')
        error.code = 'add_reload_failed'
        throw error
      }
      return { ...response, quality_reload: reloaded.status }
    }
    pending = operation().finally(() => { pending = null })
    return pending
  }
}

export function qualityAddOutcome(response) {
  if (!response || response.record_present !== true) return { key: 'ops.add.unconfirmed', tone: 'warning' }
  if (response.detail_pending) return { key: 'ops.add.pending', tone: 'warning' }
  if (response.research) {
    const result = qualityRefreshOutcome(response.research)
    return { ...result, key: `ops.refresh.${result.kind}` }
  }
  return { key: response.held ? 'ops.add.unpublished' : 'ops.add.done', tone: response.held ? 'warning' : 'success' }
}

export function manualRouteFields(values) {
  const g = key => String(values[key] ?? '').trim()
  const fields = {}
  const number = key => {
    const value = Number(g(key))
    if (!Number.isFinite(value) || value < 0) throw new Error('Enter a nonnegative number for fees and stay days.')
    return value
  }
  if (g('requirement')) fields.disposition = g('requirement')
  if (g('visa_type')) fields.visa_category = g('visa_type')
  if (g('fee_amount') || g('fee_currency')) fields.government_fee = {
    amount: g('fee_amount') ? number('fee_amount') : null,
    currency: g('fee_currency') ? g('fee_currency').toUpperCase() : null }
  for (const [input, field] of [['permitted_stay', 'permitted_stay'], ['processing_time', 'processing_time'],
    ['official_portal_url', 'official_portal_url'], ['channel_detail', 'application_channel_detail']]) {
    if (g(input)) fields[field] = g(input)
  }
  if (g('stay_days')) fields.permitted_stay_days = number('stay_days')
  if (g('required_documents')) fields.required_documents = g('required_documents').split(',').map(x => x.trim()).filter(Boolean)
  if (g('exceptions')) fields.exceptions = g('exceptions').split('\n').map(x => x.trim()).filter(Boolean)
  if (g('visa_type') && (g('validity') || g('entries_sel'))) fields.visa_products = [{
    type: g('visa_type'), entry: g('entries_sel') || null, validity: g('validity') || null,
    max_stay_days: fields.permitted_stay_days ?? null,
    fee: fields.government_fee || { amount: null, currency: null }, notes: null }]
  return fields
}
