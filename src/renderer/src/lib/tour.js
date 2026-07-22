// Builds the guided-tour step list for a chosen template case + role.
// Each step optionally runs `before()` (navigation) then spotlights a
// [data-tour="target"] element with an explanatory bubble.

export function buildTourSteps(role, c, ctrl) {
  const route = `${c.originCountry} \u2192 ${c.destinationCountry}`
  const steps = []

  steps.push({
    title: 'Welcome to Ellis',
    body: `We'll walk through ${c.applicantName}'s ${route} ${c.pathway} case as the ${role}. Tap Next to move through each part of the workspace.`
  })

  steps.push({
    target: 'nav-dashboard', placement: 'right', before: () => ctrl.nav('dashboard'),
    title: 'Home', body: 'Your live snapshot: active cases, open tasks, and documents Ellis has processed.'
  })

  if (role === 'employer') {
    steps.push({
      target: 'emp-chart', placement: 'bottom',
      title: 'Workforce compliance', body: 'A real-time compliance score for every employee. Black bars are compliant; grey bars need attention. Click any bar to open that case.'
    })
  }

  if (role !== 'counsel') {
    steps.push({
      target: 'tiles', placement: 'bottom',
      title: 'Your capabilities', body: 'The tools you use most, one click away. They change based on whether you are an immigrant, employer, or counsel.'
    })
  }

  steps.push({
    target: 'nav-cases', placement: 'right', before: () => ctrl.nav('cases'),
    title: 'Cases', body: 'Every immigration case lives here, across the USA and Canada, for work, study, and travel.'
  })

  steps.push({
    target: 'tab-overview', placement: 'bottom', before: () => { ctrl.openCase(c.id); ctrl.setCaseTab('overview') },
    title: 'Case overview', body: 'The progress bar shows exactly where the case stands. Below it: the case file at a glance and the next actions — run any task right from the list, then download the result.'
  })

  steps.push({
    target: 'tab-documents', placement: 'bottom', before: () => ctrl.setCaseTab('documents'),
    title: 'Documents', body: 'Every document as a card, with the required checklist beside it so you always know what to add next. Ellis extracts every field — and can translate any document to English.'
  })

  steps.push({
    target: 'add-doc', placement: 'left',
    title: 'Add & preview', body: 'Add a file or paste text. Click Preview on any document to read the original in full.'
  })

  steps.push({
    target: 'tab-qa', placement: 'bottom', before: () => ctrl.setCaseTab('qa'),
    title: 'Ask Ellis', body: 'Ask anything about this case. Answers are grounded only in the case file and come with citations.'
  })

  steps.push({
    target: 'tab-forms', placement: 'bottom', before: () => ctrl.setCaseTab('forms'),
    title: 'Form preparation', body: `Ellis pre-fills the correct ${c.destinationCountry} form from the case facts and exports it as a filled PDF you can download.`
  })

  if (role !== 'immigrant') {
    steps.push({
      target: 'tab-evidence', placement: 'bottom', before: () => ctrl.setCaseTab('evidence'),
      title: 'Counsel handoff', body: 'One click assembles an attorney-ready evidence packet, indexed and exportable as a PDF for final review.'
    })
  } else {
    steps.push({
      target: 'tab-travel', placement: 'bottom', before: () => ctrl.setCaseTab('travel'),
      title: 'Travel risk', body: 'Check any trip before you book it — Ellis weighs your status and pending filings and tells you go, caution, or hold.'
    })
  }

  steps.push({
    title: "You're ready",
    body: 'That\u2019s Ellis. Everything you just saw runs free and on-device, with no API keys or per-use cost. Close this and explore the case.'
  })

  return steps
}
