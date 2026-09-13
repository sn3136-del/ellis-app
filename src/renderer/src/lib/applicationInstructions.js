// Only the server's explicit source-ordered contract can supply a procedure.
// Empty, held and older unmarked responses must never revive raw phase arrays.
export function applicationInstructions(result, guidance = result?.guidance) {
  const channel = String(guidance?.application_channel || '').toLowerCase()
  const exempt = guidance?.disposition === 'VISA_EXEMPT'
    && ['', 'none', 'not_required'].includes(channel)
  if (!guidance || result?.held || exempt) {
    return { status: 'not_applicable', steps: [], sourceUrl: null }
  }
  let sourceUrl = null
  try {
    const url = new URL(result?.application_steps_source_url)
    if (url.protocol === 'https:' && !url.username && !url.password) sourceUrl = url.href
  } catch { /* Unavailable source metadata remains unavailable. */ }
  const steps = result?.apply_steps
  if (result?.application_steps_status !== 'source_ordered' || !Array.isArray(steps)
      || !steps.length || steps.some((x) => typeof x !== 'string' || !x.trim())) {
    // The published application channel is useful guidance even when no
    // officially ordered procedure exists. Keep it as prose: never invent
    // step order from legacy account/payment/submission arrays.
    const detail = guidance?.application_channel_detail
    const summary = typeof detail === 'string' ? detail.trim() : ''
    const placeholder = /^(unknown|not (yet )?confirmed|not publicly available|not applicable|n\/?a)[.!]?$/i.test(summary)
    return { status: 'unknown', steps: [], sourceUrl,
      ...(summary && !placeholder ? { summary } : {}) }
  }
  // Preserve official order and the complete conditional text; no keyword
  // sorting, sentence rewriting or maximum-item/character truncation.
  return { status: 'source_ordered', steps: steps.map((x) => x.trim()), sourceUrl }
}
