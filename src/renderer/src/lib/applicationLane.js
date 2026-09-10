// Presentation only: this never decides passport/product eligibility. The
// source-backed application instruction selects the same narrow ETA601 lane
// as kimi_primary._australian_eta_app_workflow. Its official URL is a guide,
// not an initial web application or an invented app-store/deep link.
export function applicationLane(guidance) {
  const g = guidance || {}
  const href = typeof g.official_portal_url === 'string' ? g.official_portal_url : null
  const standard = {
    kind: 'standard', href,
    sectionKey: 'db.formsPortal', linkKey: 'db.portalStart', inlineKey: 'db.portalInline',
    channelKey: null,
  }
  let source
  try { source = new URL(href) } catch { return standard }
  const etaApp = g.disposition === 'ELECTRONIC_AUTHORIZATION_REQUIRED'
    && g.requirement_detail === 'eta_electronic_authorization'
    && source.protocol === 'https:'
    && source.host === 'immi.homeaffairs.gov.au'
    && !source.username && !source.password
    && source.pathname.replace(/\/$/, '') === '/visas/getting-a-visa/visa-listing/electronic-travel-authority-601'
    && typeof g.application_channel_detail === 'string'
    && g.application_channel_detail.startsWith('Apply using the Australian ETA app,')
  return etaApp ? {
    kind: 'australian_eta_app', href,
    sectionKey: 'db.etaAppSection', linkKey: 'db.etaAppInstructions',
    inlineKey: 'db.etaAppInstructions', channelKey: 'db.etaAppChannel',
  } : standard
}

export function applicationStepLinkIndex(steps, lane) {
  if (!lane?.href || !Array.isArray(steps)) return -1
  const matches = lane.kind === 'australian_eta_app'
    ? /Australian ETA app/i
    : /register|portal|online|website|e-?visa|application form|apply/i
  return steps.findIndex((step) => typeof step === 'string' && matches.test(step))
}
