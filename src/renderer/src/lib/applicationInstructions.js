// Presentation of the published answer. Only the explicit server contract
// supplies an ordered procedure; saved application fields stay unnumbered.
const APPLICATION_FIELDS = ['account_registration_steps', 'submission_process', 'payment_process']

function instructionTexts(value) {
  return (Array.isArray(value) ? value : [value])
    .filter((text) => typeof text === 'string')
    .map((text) => text.trim())
    .filter((text) => text && !/^(?:unknown|not (?:yet )?confirmed|not publicly available|not applicable|n\/?a|(?:application )?(?:steps|instructions) (?:are |have )?(?:not (?:yet )?confirmed|unavailable)|暂无(?:申请)?(?:步骤|信息)|不适用|不適用|未公开|未公開)[.!。]?$/i.test(text)
      && !/^https?:\/\/\S+$/i.test(text)
      && !/^\[[^\]]*\]\(https?:\/\/[^)]+\)$/.test(text))
}

function noApplication(guidance) {
  return guidance?.disposition === 'VISA_EXEMPT'
    && ['', 'none', 'not_required'].includes(String(guidance?.application_channel || '').toLowerCase())
}

function savedInstructions(guidance) {
  return [...new Set(APPLICATION_FIELDS.flatMap((field) => instructionTexts(guidance?.[field])))]
}

export function applicationInstructions(result, guidance = result?.guidance) {
  if (!guidance || result?.held) {
    return { status: 'not_applicable', steps: [], sourceUrl: null }
  }
  let sourceUrl = null
  try {
    const url = new URL(result?.application_steps_source_url)
    if (url.protocol === 'https:' && !url.username && !url.password) sourceUrl = url.href
  } catch { /* Unavailable source metadata remains unavailable. */ }
  const exempt = noApplication(guidance)
  const supplied = result?.apply_steps
  const ordered = !exempt && result?.application_steps_status === 'source_ordered'
    && Array.isArray(supplied) && supplied.length > 0
    && supplied.every((text) => typeof text === 'string' && instructionTexts(text).length === 1)
  // Keep saved prose/arrays intact and in their stored order. They are not a
  // verified sequence, so the reader renders bullets rather than step numbers.
  const steps = exempt ? [] : ordered ? supplied.map((text) => text.trim()) : savedInstructions(guidance)
  const summary = exempt || ordered ? '' : instructionTexts(guidance.application_channel_detail).join('\n')
  const routeTexts = new Set([...steps, summary])
  const products = (Array.isArray(guidance.visa_products) ? guidance.visa_products : [])
    .filter((product) => product && typeof product === 'object' && !noApplication(product))
    .map((product, index) => ({
      index, name: typeof product.type === 'string' ? product.type.trim() : '',
      steps: savedInstructions(product),
      summary: instructionTexts(product.application_channel_detail).join('\n'),
    }))
    // Never promote an unnamed product's procedure to the route or a sibling.
    .filter((product) => product.name && [...product.steps, product.summary]
      .some((text) => text && !routeTexts.has(text)))
  return {
    status: ordered ? 'source_ordered' : steps.length || summary || products.length ? 'available' : exempt ? 'not_applicable' : 'unknown',
    steps, sourceUrl: exempt ? null : sourceUrl,
    ...(summary ? { summary } : {}),
    ...(products.length ? { products } : {}),
  }
}
