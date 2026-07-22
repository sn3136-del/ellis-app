import { useState, useEffect } from 'react'
import { ellis, ROLE_TABS, CAPABILITIES, VISA_TYPES, fmtDate, notifyOthers, requiredDocs, docSatisfies } from '../lib/api.js'
import { useCountUp, AnimatedDonut, AnimatedBar, StageBar, caseProgress } from '../components/charts.jsx'
import { downloadFormPdf, downloadHandoffPdf, downloadTranslationPdf, downloadFormPdfToDesktop, downloadHandoffPdfToDesktop, downloadTranslationPdfToDesktop } from '../lib/pdf.js'
import { Icon } from '../components/icons.jsx'
import { Loading, Findings, KVList, Checklist, Empty, ErrorNote, Sev, useToast } from '../components/ui.jsx'
import PortalModal from '../components/PortalModal.jsx'

// Government filing portals (label only; the modal handles sign-in + status).
const GOV_PORTAL = {
  Canada: { label: 'IRCC' },
  USA: { label: 'USCIS' }
}

// Email counsel: generate the handoff PDF, draft an email with a login link, and notify counsel.
async function notifyCounsel({ c, role, toast, onNotify }) {
  toast('Preparing counsel handoff…')
  const res = await ellis.ai.evidencePacket({ case: c })
  if (res.ok) { try { await downloadHandoffPdf(res.data, c) } catch (e) {} }
  const subject = `Ellis case update — ${c.applicantName} (${c.originCountry || ''} → ${c.destinationCountry})`
  const body = [
    'Hi counsel,',
    '',
    `There is new progress on ${c.applicantName}'s ${c.pathway} case${c.visaType ? ` (${c.visaType})` : ''}.`,
    'The counsel handoff packet (PDF) has been generated and saved for your review.',
    '',
    'Open the full case in Ellis to review documents, risks, and the evidence index:',
    `Ellis → Cases → ${c.applicantName}  (sign in: admin / 1234)`,
    '',
    '— Sent automatically from Ellis'
  ].join('\n')
  try { await ellis.openExternal(`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`) } catch (e) {}
  await notifyOthers(role, c, `Counsel notified — handoff packet ready for ${c.applicantName}`, ['counsel'])
  onNotify?.()
  toast('Counsel notified · email drafted with packet attached')
}

const TAB_META = {
  overview: { label: 'Overview', icon: 'flow' },
  documents: { label: 'Documents', icon: 'doc' },
  qa: { label: 'Ask Ellis', icon: 'chat' },
  notices: { label: 'Notices / RFEs', icon: 'bell' },
  forms: { label: 'Forms', icon: 'form' },
  evidence: { label: 'Evidence', icon: 'folder' },
  compliance: { label: 'Compliance', icon: 'gauge' },
  travel: { label: 'Travel', icon: 'plane' },
  authenticity: { label: 'Authenticity', icon: 'shield' }
}

export default function CaseWorkspace({ caseId, role, profile, tab, onTab, onBack, onSettings, onNotify }) {
  const [c, setC] = useState(null)
  const [portal, setPortal] = useState(false)
  const tabs = ROLE_TABS[role] || ROLE_TABS.counsel
  const activeTab = tabs.includes(tab) ? tab : tabs[0]
  const setTab = onTab

  useEffect(() => { ellis.getCase(caseId).then(setC) }, [caseId])

  async function patch(p) {
    const updated = await ellis.updateCase(caseId, p)
    setC(updated)
    return updated
  }

  if (!c) return <div className="page"><Loading label="Opening case" /></div>

  return (
    <div>
      <div className="topbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <button className="iconbtn" onClick={onBack}><Icon.back /></button>
          <div>
            <h1>{c.applicantName}</h1>
            <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>
              {c.originCountry || 'Origin'} → {c.destinationCountry} · {c.pathway} {c.visaType && `· ${c.visaType}`}
            </div>
          </div>
        </div>
      </div>

      <div className="page page--wide">
        <div className="tabs">
          {tabs.map((t) => {
            const m = TAB_META[t]
            return (
              <button key={t} data-tour={'tab-' + t} className={'tab' + (activeTab === t ? ' is-active' : '')} onClick={() => setTab(t)}>
                {m.label}
              </button>
            )
          })}
        </div>

        <div className="tabpanel" key={activeTab}>
          {activeTab === 'overview' && <Overview c={c} role={role} patch={patch} onSettings={onSettings} onNotify={onNotify} onConnect={() => setPortal(true)} />}
          {activeTab === 'documents' && <Documents c={c} patch={patch} onSettings={onSettings} />}
          {activeTab === 'qa' && <AskTab c={c} role={role} patch={patch} onSettings={onSettings} onNotify={onNotify} />}
          {activeTab === 'notices' && <NoticesTab c={c} onSettings={onSettings} />}
          {activeTab === 'forms' && <FormsTab c={c} role={role} onSettings={onSettings} onConnect={() => setPortal(true)} />}
          {activeTab === 'evidence' && <EvidenceTab c={c} role={role} onSettings={onSettings} onNotify={onNotify} />}
          {activeTab === 'compliance' && <ComplianceTab c={c} onSettings={onSettings} />}
          {activeTab === 'travel' && <TravelTab c={c} onSettings={onSettings} />}
          {activeTab === 'authenticity' && <AuthenticityTab c={c} onSettings={onSettings} />}
        </div>
      </div>

      {portal && <PortalModal jurisdiction={c.destinationCountry === 'Canada' ? 'Canada' : 'USA'} c={c} onClose={() => setPortal(false)} />}
    </div>
  )
}

/* ---------------- Overview + lifecycle ---------------- */
function Overview({ c, role, patch, onSettings, onNotify, onConnect }) {
  const toast = useToast()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [notes, setNotes] = useState(c.notes || '')
  const [showDone, setShowDone] = useState(false)

  // Auto-run the lifecycle the first time a case has no tasks — no Run click needed.
  useEffect(() => {
    if ((c.tasks || []).length === 0 && !loading) runLifecycle(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function runLifecycle(silent) {
    setLoading(true); setError(null)
    const res = await ellis.ai.lifecyclePlan({ case: c })
    setLoading(false)
    if (!res.ok) return setError(res.error)
    const tasks = (res.data.tasks || []).map((t, i) => ({ id: 'task_' + Date.now() + '_' + i, ...t, status: 'open' }))
    await patch({ tasks, facts: { ...c.facts, stage: res.data.stage } })
    if (!silent) toast('Lifecycle plan regenerated')
  }

  const tasks = c.tasks || []
  const openTasks = tasks.filter((t) => t.status !== 'done')
  const doneTasks = tasks.filter((t) => t.status === 'done')
  const pct = caseProgress(c)
  const nDocs = useCountUp((c.documents || []).length)
  const nOpen = useCountUp(openTasks.length)
  const nDone = useCountUp(doneTasks.length)
  const portalLabel = (GOV_PORTAL[c.destinationCountry] || GOV_PORTAL.Canada).label

  return (
    <div>
      {/* Progress: one calm card with the progression bar, stage, and key numbers */}
      <div className="card" style={{ padding: '24px 26px', marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 18 }}>
          <div style={{ flex: 1, minWidth: 300 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
              <div className="eyebrow">Case progress</div>
              <span style={{ fontSize: 13, fontWeight: 700 }}>{pct}%</span>
            </div>
            <AnimatedBar value={pct} height={9} />
            <div style={{ marginTop: 20 }}>
              <StageBar c={c} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 0 }}>
            {[[nDocs, 'documents'], [nOpen, 'open tasks'], [nDone, 'completed']].map(([n, cap], i) => (
              <div key={cap} style={{ padding: '2px 22px', borderLeft: i ? '1px solid var(--line)' : 'none', textAlign: 'left' }}>
                <div style={{ fontSize: 28, fontWeight: 800, lineHeight: 1 }}>{n}</div>
                <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 5 }}>{cap}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-2" style={{ alignItems: 'start' }}>
        <div>
          {/* Compact case facts */}
          <div className="card" style={{ padding: 22, marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <div className="eyebrow">Case file</div>
              <span className="chip">{c.facts?.stage || 'New'}</span>
            </div>
            <KVList fields={[
              { label: 'Route', value: `${c.originCountry || '—'} → ${c.destinationCountry}` },
              { label: 'Visa / permit', value: c.visaType || c.pathway },
              ...(c.employer ? [{ label: 'Employer', value: c.employer }] : []),
              ...(c.facts?.['Receipt Number'] ? [{ label: 'Receipt', value: c.facts['Receipt Number'] }] : []),
              { label: 'Opened', value: fmtDate(c.createdAt) }
            ]} />
            <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
              <button className="btn btn--ghost btn--sm" onClick={() => runLifecycle(false)} disabled={loading}>
                {loading ? <><span className="spinner spinner--ink" /> Working</> : <><Icon.spark style={{ width: 14, height: 14 }} /> Regenerate plan</>}
              </button>
              {role !== 'counsel' && (
                <button className="btn btn--ghost btn--sm" onClick={onConnect}>
                  <Icon.plug style={{ width: 14, height: 14 }} /> Connect to {portalLabel}
                </button>
              )}
              {role !== 'counsel' && (
                <button className="btn btn--ghost btn--sm" onClick={() => notifyCounsel({ c, role, toast, onNotify })}>
                  <Icon.mail style={{ width: 14, height: 14 }} /> Notify counsel
                </button>
              )}
            </div>
          </div>

          <div className="card" style={{ padding: 22 }}>
            <div className="eyebrow">Case notes</div>
            <textarea className="textarea" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Internal notes for this case..." />
            <button className="btn btn--sm" style={{ marginTop: 10 }} onClick={() => { patch({ notes }); toast('Notes saved') }}>Save notes</button>
          </div>
        </div>

        <div>
          <ErrorNote error={error} onSettings={onSettings} />
          <div className="eyebrow" style={{ marginBottom: 10 }}>Next actions</div>
          {tasks.length === 0 && <Loading label="Ellis is building the task plan" />}
          {openTasks.map((t) => <TaskRow key={t.id} t={t} c={c} role={role} patch={patch} toast={toast} onNotify={onNotify} />)}
          {tasks.length > 0 && openTasks.length === 0 && (
            <div className="card card--soft" style={{ padding: 16, fontSize: 13.5, color: 'var(--muted)' }}>
              Every task is complete. Ellis is monitoring the case and will surface the next action automatically.
            </div>
          )}
          {doneTasks.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <button className="linkbtn" onClick={() => setShowDone(!showDone)}>
                {showDone ? 'Hide' : 'Show'} completed ({doneTasks.length})
              </button>
              {showDone && <div style={{ marginTop: 10 }}>{doneTasks.map((t) => <TaskRow key={t.id} t={t} c={c} role={role} patch={patch} toast={toast} onNotify={onNotify} />)}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function primaryForm(c) {
  if (c.destinationCountry === 'Canada') {
    if (c.pathway === 'work') return 'IMM 1295'
    if (c.pathway === 'student') return 'IMM 1294'
    return 'IMM 5257'
  }
  if (c.pathway === 'work') return 'Form I-129'
  if (c.pathway === 'student') return 'Form I-20 / DS-160'
  return 'Form DS-160'
}

// Infer which Ellis capability a task maps to, run it, and produce a downloadable artifact when relevant.
async function runTask(t, c) {
  const title = (t.title || '').toLowerCase()
  if (/translat/.test(title)) {
    const cn = (c.documents || []).find((d) => /[\u3400-\u9FFF]/.test(d.text || '')) || (c.documents || [])[0]
    const res = await ellis.ai.translateDocument({ text: cn?.text || '', targetLanguage: 'English' })
    return res.ok ? { summary: `Translated ${cn?.name || 'document'} (${res.data.sourceLanguage} → ${res.data.targetLanguage}).` } : { error: res.error }
  }
  if (/evidence|handoff|packet|bundle|counsel/.test(title)) {
    const res = await ellis.ai.evidencePacket({ case: c })
    return res.ok ? { summary: `Assembled counsel handoff packet with ${(res.data.exhibits || []).length} exhibit(s).`, artifactKind: 'handoff', artifactData: res.data } : { error: res.error }
  }
  if (/complian|audit/.test(title)) {
    const res = await ellis.ai.complianceAudit({ case: c })
    return res.ok ? { summary: `Compliance audit complete — score ${res.data.score}/100.` } : { error: res.error }
  }
  if (/risk|flag/.test(title)) {
    const res = await ellis.ai.riskFlags({ case: c })
    const n = (res.data?.findings || []).filter((f) => f.severity !== 'info').length
    return res.ok ? { summary: `Risk review complete — ${n} item(s) to address.` } : { error: res.error }
  }
  if (/form|file|filing|petition|prepare|application|submit|i-129|imm|ds-160|i-20|lca/.test(title)) {
    const res = await ellis.ai.prepareForm({ formType: primaryForm(c), case: c })
    return res.ok ? { summary: `Prepared ${res.data.formName} from the case file.`, artifactKind: 'form', artifactData: res.data } : { error: res.error }
  }
  return { summary: 'Marked complete by Ellis.' }
}

function TaskRow({ t, c, role, patch, toast, onNotify }) {
  const done = t.status === 'done'
  const [busy, setBusy] = useState(false)
  const [dlBusy, setDlBusy] = useState(false)

  function toggle() {
    const tasks = c.tasks.map((x) => x.id === t.id ? { ...x, status: done ? 'open' : 'done' } : x)
    patch({ tasks })
  }

  async function perform() {
    setBusy(true)
    const out = await runTask(t, c)
    setBusy(false)
    if (out.error) { toast?.('Could not complete: ' + (out.error.message || out.error)); return }
    const tasks = c.tasks.map((x) => x.id === t.id ? { ...x, status: 'done', result: out.summary, artifactKind: out.artifactKind || null, artifactData: out.artifactData || null } : x)
    await patch({ tasks })
    await notifyOthers(role, c, `${t.title} — completed by ${role}`)
    onNotify?.()
    toast?.(out.summary)
  }

  async function download() {
    setDlBusy(true)
    if (t.artifactKind === 'form') await downloadFormPdf(t.artifactData, c)
    else if (t.artifactKind === 'handoff') await downloadHandoffPdf(t.artifactData, c)
    setDlBusy(false)
  }

  return (
    <div className="row" style={{ flexWrap: 'wrap' }}>
      <button className="iconbtn" style={{ width: 26, height: 26, background: done ? 'var(--ink)' : 'transparent', color: '#fff' }} onClick={toggle}>
        {done && <Icon.check style={{ width: 14, height: 14 }} />}
      </button>
      <div className="row__main">
        <div className="row__title" style={{ textDecoration: done ? 'line-through' : 'none', opacity: done ? 0.5 : 1 }}>{t.title}</div>
        <div className="row__sub">{t.owner} {t.due && `· due ${t.due}`} {t.result ? `· ${t.result}` : (t.why ? `· ${t.why}` : '')}</div>
      </div>
      {t.priority && !done && <span className="chip">{t.priority}</span>}
      {!done && <button className="btn btn--sm" onClick={perform} disabled={busy}>{busy ? <><span className="spinner" /> Working</> : <><Icon.play style={{ width: 13, height: 13 }} /> Run</>}</button>}
      {done && t.artifactKind && <button className="btn btn--ghost btn--sm" onClick={download} disabled={dlBusy}>{dlBusy ? <><span className="spinner spinner--ink" /> …</> : <><Icon.download style={{ width: 14, height: 14 }} /> Download</>}</button>}
    </div>
  )
}

/* ---------------- Documents ---------------- */
// Pick an icon that matches what the document is.
function docIcon(name) {
  const n = (name || '').toLowerCase()
  if (/passport/.test(n)) return 'globe'
  if (/notice|i-797|rfe|receipt/.test(n)) return 'bell'
  if (/permit|visa|i-94|ead|i-20|sevis|lca|imm |form/.test(n)) return 'form'
  if (/offer|employment|letter|agreement|resolution/.test(n)) return 'mail'
  if (/resume|cv|degree|credential|transcript|evaluation/.test(n)) return 'user'
  if (/fund|bank|gic|tuition|pay/.test(n)) return 'folder'
  return 'doc'
}

function Documents({ c, patch, onSettings }) {
  const toast = useToast()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)
  const [preview, setPreview] = useState(null)
  const [xlate, setXlate] = useState(null)
  const [showEn, setShowEn] = useState(false)

  function openPreview(d) { setPreview(d); setXlate(null); setShowEn(false) }
  async function translate(d) {
    if (xlate?.data) { setShowEn(true); return }
    setXlate({ loading: true })
    const res = await ellis.ai.translateDocument({ text: d.text || '', targetLanguage: 'English' })
    setXlate(res.ok ? { data: res.data } : { error: res.error })
    if (res.ok) setShowEn(true)
  }

  async function pickFiles() {
    const files = await ellis.pickDocuments()
    if (!files.length) return
    const docs = files.map((f) => ({ id: 'doc_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6), name: f.name, text: f.text, addedAt: Date.now(), extracted: null }))
    await patch({ documents: [...(c.documents || []), ...docs] })
    toast(`${files.length} document(s) added`)
  }

  async function addPasted() {
    if (!text.trim()) return
    const doc = { id: 'doc_' + Date.now(), name: name.trim() || 'Pasted document', text, addedAt: Date.now(), extracted: null }
    await patch({ documents: [...(c.documents || []), doc] })
    setAdding(false); setName(''); setText('')
    toast('Document added')
  }

  async function review(doc) {
    setBusyId(doc.id); setError(null)
    const res = await ellis.ai.extractDocument({
      documentName: doc.name, text: doc.text,
      originCountry: c.originCountry, destinationCountry: c.destinationCountry, pathway: c.pathway
    })
    setBusyId(null)
    if (!res.ok) return setError(res.error)
    const documents = c.documents.map((d) => d.id === doc.id ? { ...d, extracted: res.data } : d)
    // merge extracted fields into case facts
    const facts = { ...c.facts }
    ;(res.data.fields || []).forEach((f) => { if (f.value && f.value !== 'MISSING') facts[f.label] = f.value })
    await patch({ documents, facts })
    toast('Document reviewed')
  }

  function remove(id) {
    patch({ documents: c.documents.filter((d) => d.id !== id) })
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 700 }}>Documents</h2>
          <p style={{ color: 'var(--muted)', fontSize: 14 }}>Add passports, notices, permits, pay stubs, or any case file. Ellis extracts every field and flags gaps.</p>
        </div>
        <div style={{ display: 'flex', gap: 10 }} data-tour="add-doc">
          <button className="btn btn--ghost" onClick={pickFiles}>Add file</button>
          <button className="btn" onClick={() => setAdding(true)}><Icon.plus style={{ width: 16, height: 16 }} /> Paste text</button>
        </div>
      </div>

      <ErrorNote error={error} onSettings={onSettings} />

      {adding && (
        <div className="card" style={{ padding: 20, marginBottom: 16 }}>
          <div className="field"><label>Document name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Passport, I-797, Work permit" /></div>
          <div className="field"><label>Document text</label><textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the document contents here..." /></div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn" onClick={addPasted} disabled={!text.trim()}>Add document</button>
            <button className="btn btn--ghost" onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </div>
      )}

      {(() => {
        const docs = c.documents || []
        const required = requiredDocs(c)
        const missing = required.filter((r) => !docSatisfies(docs, r))
        const collected = required.length - missing.length
        return (
          <div className="grid grid-2" style={{ alignItems: 'start', gridTemplateColumns: '1.6fr 1fr' }}>
            {/* Left: visual document menu */}
            <div>
              {docs.length === 0 && !adding
                ? <Empty title="No documents yet" sub="Add a file or paste text to begin building this case." />
                : (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
                    {docs.map((d, i) => (
                      <div key={d.id} className="card doccard" style={{ padding: 16, cursor: 'pointer', animationDelay: `${Math.min(i, 8) * 40}ms` }} onClick={() => openPreview(d)}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                          <div className="captile__icon" style={{ width: 36, height: 36 }}>{(() => { const I = Icon[docIcon(d.name)]; return <I style={{ width: 17, height: 17 }} /> })()}</div>
                          {d.extracted
                            ? <span className="chip" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon.check style={{ width: 11, height: 11 }} /> Reviewed</span>
                            : <span className="chip">New</span>}
                        </div>
                        <div style={{ fontWeight: 600, fontSize: 13.5, lineHeight: 1.35, marginBottom: 3 }}>{d.name}</div>
                        <div style={{ fontSize: 11.5, color: 'var(--muted)', marginBottom: 12, minHeight: 15 }}>
                          {d.extracted ? (d.extracted.docType || 'Reviewed') : `${((d.text || '').length / 1000).toFixed(1)}k characters`}
                        </div>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
                          {!d.extracted && (
                            <button className="btn btn--sm" data-tour="review-doc" onClick={() => review(d)} disabled={busyId === d.id} style={{ flex: 1, justifyContent: 'center' }}>
                              {busyId === d.id ? <><span className="spinner" /> Reviewing</> : 'Review'}
                            </button>
                          )}
                          <button className="iconbtn" title="Preview" onClick={() => openPreview(d)}><Icon.eye style={{ width: 15, height: 15 }} /></button>
                          <button className="iconbtn" title="Translate to English" onClick={() => { openPreview(d); setTimeout(() => translate(d), 0) }}><Icon.globe style={{ width: 15, height: 15 }} /></button>
                          <button className="iconbtn" title="Remove" onClick={() => remove(d.id)}><Icon.trash style={{ width: 15, height: 15 }} /></button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
            </div>

            {/* Right: what's required next */}
            <div className="card" style={{ padding: 20 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
                <div className="eyebrow">Required for {c.visaType || c.pathway}</div>
                <span style={{ fontSize: 12.5, fontWeight: 700 }}>{collected}/{required.length}</span>
              </div>
              <AnimatedBar value={(collected / required.length) * 100} height={7} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginTop: 14 }}>
                {required.map((r) => {
                  const have = !missing.includes(r)
                  return (
                    <div key={r} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '7px 0', borderBottom: '1px solid var(--line)' }}>
                      <span style={{ width: 18, height: 18, borderRadius: '50%', flexShrink: 0, display: 'grid', placeItems: 'center', background: have ? 'var(--ink)' : 'transparent', border: have ? 'none' : '1.5px solid var(--line-strong)', color: '#fff' }}>
                        {have && <Icon.check style={{ width: 10, height: 10 }} />}
                      </span>
                      <span style={{ fontSize: 13, color: have ? 'var(--ink)' : 'var(--muted)', fontWeight: have ? 500 : 400 }}>{r}</span>
                    </div>
                  )
                })}
              </div>
              {missing.length > 0
                ? <p style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 12 }}>Next: add <strong style={{ color: 'var(--ink)' }}>{missing[0]}</strong>{missing.length > 1 ? ` and ${missing.length - 1} more` : ''}. Ellis reviews each document the moment it arrives.</p>
                : <p style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 12 }}>Standard document set complete. Ellis is watching for anything that expires.</p>}
            </div>
          </div>
        )
      })()}

      {preview && (
        <div className="overlay" onClick={() => setPreview(null)}>
          <div className="modal" style={{ maxWidth: 680 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <h2 style={{ marginBottom: 0 }}>{preview.name}</h2>
              <button className="iconbtn" onClick={() => setPreview(null)}>✕</button>
            </div>
            <div className="modal__sub">{preview.extracted ? `${preview.extracted.docType} · ${preview.extracted.jurisdiction || ''}` : 'Document preview'}</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <div className="eyebrow">{showEn && xlate?.data ? `English translation (${xlate.data.sourceLanguage} → English)` : 'Original document'}</div>
              <div style={{ display: 'flex', gap: 8 }}>
                {!showEn && <button className="btn btn--ghost btn--sm" onClick={() => translate(preview)} disabled={xlate?.loading}>{xlate?.loading ? <><span className="spinner spinner--ink" /> Translating</> : <><Icon.globe style={{ width: 14, height: 14 }} /> Translate to English</>}</button>}
                {showEn && <button className="btn btn--ghost btn--sm" onClick={() => setShowEn(false)}>Show original</button>}
                {xlate?.data && <button className="btn btn--ghost btn--sm" onClick={() => downloadTranslationPdf(xlate.data, preview.name, c)}><Icon.download style={{ width: 14, height: 14 }} /> Translated PDF</button>}
              </div>
            </div>

            {xlate?.error && <ErrorNote error={xlate.error} />}

            <div style={{ background: 'var(--bg-soft)', border: '1px solid var(--line)', borderRadius: 12, padding: 16, maxHeight: '46vh', overflowY: 'auto', fontFamily: showEn ? 'inherit' : 'ui-monospace, Menlo, monospace', fontSize: showEn ? 13 : 12.5, lineHeight: 1.6, whiteSpace: 'pre-wrap', userSelect: 'text' }}>
              {showEn && xlate?.data ? xlate.data.translation : (preview.text || '(empty document)')}
            </div>

            {showEn && xlate?.data && (xlate.data.glossary || []).length > 0 && (
              <div style={{ marginTop: 14 }}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>Glossary</div>
                <KVList fields={xlate.data.glossary.map((g) => ({ label: g.term, value: g.meaning }))} />
              </div>
            )}

            {preview.extracted && (
              <div style={{ marginTop: 16 }}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>Extracted by Ellis</div>
                {preview.extracted.summary && <p className="prose" style={{ marginBottom: 10, fontSize: 13.5 }}>{preview.extracted.summary}</p>}
                <KVList fields={preview.extracted.fields} />
                {(preview.extracted.flags || []).length > 0 && (
                  <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {preview.extracted.flags.map((f, i) => (
                      <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 13 }}>
                        <Sev level={f.severity} /> <span>{f.note}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div className="modal__foot">
              {!preview.extracted && <button className="btn" onClick={() => { review(preview); setPreview(null) }}>Review with Ellis</button>}
              <button className="btn btn--ghost" onClick={() => setPreview(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------------- Ask Ellis (case Q&A + command execution) ---------------- */
// Map a free-text instruction to an Ellis capability.
function parseCommand(text) {
  const t = text.toLowerCase()
  const isCmd = /\b(run|prepare|build|generate|check|assess|do|create|make|fill|complete|file|filing|translate|screen|audit|summari[sz]e|pull|give me|get|download|save|export|put|submit|send)\b/.test(t)
  // "file the I-129 then submit it to USCIS on their behalf" -> full filing flow
  if (/\b(submit|file)\b/.test(t) && /(uscis|ircc|portal|government|on (his|her|their|my) behalf|behalf)/.test(t)) return 'fileSubmit'
  if (/\bsubmit\b/.test(t) && /(form|petition|application|i-?129|i-?539|i-?765|ds-?160|imm ?\d|it\b|them\b)/.test(t)) return 'fileSubmit'
  if (/risk|flag/.test(t) && (isCmd || /risk/.test(t))) return 'risks'
  if (/complian|audit/.test(t)) return 'compliance'
  if (/evidence|handoff|packet|bundle/.test(t)) return 'evidence'
  if (/authentic|forg|fraud|genuine|tamper/.test(t)) return 'authenticity'
  if (/translat/.test(t)) return 'translation'
  if (/travel|trip|re-?enter|leave the country|fly home/.test(t) && isCmd) return 'travel'
  if (/(form|i-?129|i-?539|i-?765|i-?20|imm ?\d|ds-?160|petition|application|work permit|study permit|visitor visa|visa application)/.test(t) && isCmd) return 'forms'
  if (/lifecycle|action plan|next steps|game ?plan/.test(t)) return 'lifecycle'
  return null
}

// Pick a specific form when the user named one; otherwise the case's primary form.
function formFromText(text, c) {
  const t = (text || '').toLowerCase()
  if (/i-?129/.test(t)) return 'Form I-129'
  if (/i-?539/.test(t)) return 'Form I-539'
  if (/i-?765/.test(t)) return 'Form I-765'
  if (/i-?20/.test(t)) return 'Form I-20'
  if (/ds-?160/.test(t)) return 'Form DS-160'
  if (/1295/.test(t)) return 'IMM 1295'
  if (/1294/.test(t)) return 'IMM 1294'
  if (/5257/.test(t)) return 'IMM 5257'
  if (/work permit/.test(t)) return c.destinationCountry === 'Canada' ? 'IMM 1295' : 'Form I-129'
  if (/study permit/.test(t)) return 'IMM 1294'
  if (/visitor visa|visit visa|trv/.test(t)) return 'IMM 5257'
  return primaryForm(c)
}
function wantsDesktop(text) { return /desktop|\bdownload\b|\bsave\b|\bexport\b|to my (laptop|computer|mac|machine)/.test((text || '').toLowerCase()) }

async function executeCommand(cap, c, text = '') {
  if (cap === 'risks') {
    const r = await ellis.ai.riskFlags({ case: c }); if (!r.ok) return { error: r.error }
    const items = (r.data.findings || []).filter((f) => f.severity !== 'info')
    return { answer: items.length ? `I ran the risk flags against the current documents. ${items.length} item(s) need attention:\n\n` + items.map((f) => `• ${f.title} — ${f.detail || f.recommendation || ''}`).join('\n') : 'I ran the risk flags — no blocking risks against the current documents.', ran: 'Risk flags' }
  }
  if (cap === 'compliance') {
    const r = await ellis.ai.complianceAudit({ case: c }); if (!r.ok) return { error: r.error }
    return { answer: `Compliance audit complete — score ${r.data.score}/100. ${r.data.summary}`, ran: 'Compliance audit' }
  }
  if (cap === 'evidence') {
    const r = await ellis.ai.evidencePacket({ case: c }); if (!r.ok) return { error: r.error }
    const base = `I assembled the counsel handoff packet: ${(r.data.exhibits || []).length} exhibit(s), ${(r.data.openItems || []).length} open item(s) for counsel.`
    if (wantsDesktop(text)) {
      const saved = await downloadHandoffPdfToDesktop(r.data, c)
      if (saved?.ok) return { answer: `${base}\n\nSaved to your Desktop:\n${saved.path}`, ran: 'Evidence packet · saved to Desktop', reveal: saved.path, download: { kind: 'handoff', data: r.data } }
    }
    return { answer: `${base} Download the PDF below.`, ran: 'Evidence packet', download: { kind: 'handoff', data: r.data } }
  }
  if (cap === 'authenticity') {
    const doc = (c.documents || [])[0]
    const r = await ellis.ai.authenticityCheck({ text: doc?.text || '' }); if (!r.ok) return { error: r.error }
    return { answer: `Authenticity screen on ${doc?.name || 'the document'}: ${r.data.verdict} (score ${r.data.score}/100). ${r.data.summary}`, ran: 'Authenticity' }
  }
  if (cap === 'translation') {
    const doc = (c.documents || []).find((d) => /[\u3400-\u9FFF]/.test(d.text || '')) || (c.documents || [])[0]
    const r = await ellis.ai.translateDocument({ text: doc?.text || '', targetLanguage: 'English' }); if (!r.ok) return { error: r.error }
    const body = `Translated ${doc?.name || 'the document'} (${r.data.sourceLanguage} → ${r.data.targetLanguage}):\n\n${r.data.translation}`
    if (wantsDesktop(text)) {
      const saved = await downloadTranslationPdfToDesktop(r.data, doc?.name, c)
      if (saved?.ok) return { answer: `${body}\n\nSaved to your Desktop:\n${saved.path}`, ran: 'Translation · saved to Desktop', reveal: saved.path, download: { kind: 'translation', data: r.data, docName: doc?.name } }
    }
    return { answer: body, ran: 'Translation', download: { kind: 'translation', data: r.data, docName: doc?.name } }
  }
  if (cap === 'travel') {
    const r = await ellis.ai.travelRisk({ trip: {}, case: c }); if (!r.ok) return { error: r.error }
    return { answer: `Travel assessment: ${String(r.data.recommendation).toUpperCase()} (re-entry risk ${r.data.reentryRisk}). ${r.data.summary}`, ran: 'Travel risk' }
  }
  if (cap === 'forms') {
    const ft = formFromText(text, c)
    await new Promise((r) => setTimeout(r, 900)) // let the chat show it working
    const r = await ellis.ai.prepareForm({ formType: ft, case: c }); if (!r.ok) return { error: r.error }
    const miss = (r.data.missingInfo || []).length
    const missTxt = miss ? `${miss} field(s) still need input before filing.` : 'All required fields are completed from the case file.'
    if (wantsDesktop(text)) {
      const saved = await downloadFormPdfToDesktop(r.data, c)
      if (saved?.ok) return { answer: `Done. I filled out ${r.data.formName} from ${c.applicantName}'s case file and saved it to your Desktop:\n\n${saved.path}\n\n${missTxt}`, ran: 'Form prep · saved to Desktop', reveal: saved.path, download: { kind: 'form', data: r.data } }
      return { answer: `I filled out ${r.data.formName}, but couldn't write to the Desktop automatically. Use the download button below.`, ran: 'Form prep', download: { kind: 'form', data: r.data } }
    }
    return { answer: `I prepared ${r.data.formName} from the case file. ${missTxt} Download the filled form below.`, ran: 'Form prep', download: { kind: 'form', data: r.data } }
  }
  if (cap === 'lifecycle') {
    const r = await ellis.ai.lifecyclePlan({ case: c }); if (!r.ok) return { error: r.error }
    return { answer: `${c.applicantName} is at the ${r.data.stage} stage. Next actions:\n\n` + (r.data.tasks || []).slice(0, 5).map((t) => `• ${t.title} (${t.owner}${t.due ? `, due ${t.due}` : ''})`).join('\n'), ran: 'Lifecycle plan' }
  }
  if (cap === 'fileSubmit') {
    // Full agent flow: fill the form from the case file, then hand off to the
    // government portal for credentialed submission.
    const ft = formFromText(text, c)
    const portal = c.destinationCountry === 'Canada' ? 'IRCC' : 'USCIS'
    await new Promise((r) => setTimeout(r, 900))
    const r = await ellis.ai.prepareForm({ formType: ft, case: c }); if (!r.ok) return { error: r.error }
    const miss = (r.data.missingInfo || []).length
    return {
      answer: `Step 1 done — I filled out ${r.data.formName} from ${c.applicantName}'s case file${miss ? ` (${miss} field(s) flagged for review)` : ' with every required field completed'} and attached the supporting evidence index.\n\nStep 2 — to submit it to ${portal} on ${c.applicantName}'s behalf I need to sign in to the ${portal} portal. Enter the account credentials in the window that just opened, and I'll upload, validate, and file the petition, then report the receipt number back here.`,
      ran: `Form prep → ${portal} filing`,
      download: { kind: 'form', data: r.data },
      submit: { formName: r.data.formName, portal }
    }
  }
  return null
}

// Download button rendered in the chat after Ellis produces a downloadable artifact.
function AskDownload({ dl, c }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const label = dl.kind === 'form' ? `Download ${dl.data?.formName || 'filled form'} (PDF)` : dl.kind === 'handoff' ? 'Download counsel handoff (PDF)' : 'Download translation (PDF)'
  async function go() {
    setBusy(true)
    try {
      let res
      if (dl.kind === 'form') res = await downloadFormPdf(dl.data, c)
      else if (dl.kind === 'handoff') res = await downloadHandoffPdf(dl.data, c)
      else res = await downloadTranslationPdf(dl.data, dl.docName, c)
      if (res?.ok) toast('Saved to your laptop')
      else if (!res?.canceled) toast('Could not save the PDF')
    } catch (e) { toast('Could not save the PDF') }
    setBusy(false)
  }
  return (
    <div style={{ marginTop: 12 }}>
      <button className="btn btn--sm" onClick={go} disabled={busy}>{busy ? <><span className="spinner" /> Preparing</> : <><Icon.download style={{ width: 15, height: 15 }} /> {label}</>}</button>
    </div>
  )
}

function AskTab({ c, patch, onSettings }) {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [submitFlow, setSubmitFlow] = useState(null) // { formName, portal } while awaiting portal login
  const msgs = c.messages || []
  const jurisdiction = c.destinationCountry === 'Canada' ? 'Canada' : 'USA'

  async function ask(question) {
    const text = (question || q).trim()
    if (!text) return
    setQ(''); setLoading(true); setError(null)
    const cap = parseCommand(text)
    let entry
    if (cap) {
      const out = await executeCommand(cap, c, text)
      if (out?.error) { setLoading(false); return setError(out.error) }
      entry = { id: 'm_' + Date.now(), question: text, answer: out.answer, ran: out.ran, download: out.download || null, reveal: out.reveal || null, citations: [], followUps: out.submit ? [] : ['Run the risk flags', 'Check compliance', `Fill out ${primaryForm(c)} and save to my desktop`, `File ${primaryForm(c)} and submit it to ${jurisdiction === 'Canada' ? 'IRCC' : 'USCIS'}`] }
      if (out.submit) setSubmitFlow(out.submit)
    } else {
      const res = await ellis.ai.answerQuestion({ case: c, question: text })
      if (!res.ok) { setLoading(false); return setError(res.error) }
      entry = { id: 'm_' + Date.now(), question: text, ...res.data }
    }
    setLoading(false)
    await patch({ messages: [...msgs, entry] })
  }

  // Called when the portal filing completes: record the receipt on the case,
  // advance the stage, notify the other roles, and report back in the chat.
  async function onSubmitted(receipt) {
    const portal = submitFlow?.portal || (jurisdiction === 'Canada' ? 'IRCC' : 'USCIS')
    const formName = submitFlow?.formName || primaryForm(c)
    setSubmitFlow(null)
    const entry = {
      id: 'm_' + Date.now(), question: `— ${portal} submission —`,
      answer: `Filed. ${formName} for ${c.applicantName} was submitted to ${portal} successfully.\n\n• Receipt number: ${receipt}\n• Status: ${jurisdiction === 'Canada' ? 'Application received' : 'Case Was Received'}\n• Submitted: ${new Date().toLocaleString()}\n\nI updated the case stage to Filing and will monitor the ${portal} portal — you'll get a notification the moment the status changes.`,
      ran: `${portal} filing submitted`, citations: [], followUps: ['Run the risk flags', 'What happens next?', 'Build the counsel handoff']
    }
    await patch({
      messages: [...(c.messages || []), entry],
      facts: { ...(c.facts || {}), stage: 'Filing', 'Receipt Number': receipt, 'Filed Via': `Ellis → ${portal} (${new Date().toLocaleDateString()})` }
    })
    await notifyOthers('employer', c, `${formName} filed with ${portal} for ${c.applicantName} — receipt ${receipt}`)
  }

  const SUGGESTED = ['Run the risk flags using current documentation', 'What documents are still missing?', `File ${primaryForm(c)} and submit it to ${jurisdiction === 'Canada' ? 'IRCC' : 'USCIS'} on their behalf`, `Prepare ${primaryForm(c)}`, 'Build the counsel handoff', 'When does status expire?']

  return (
    <div>
      <h2 style={{ fontSize: 20, fontWeight: 700 }}>Ask Ellis</h2>
      <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 18 }}>Ask anything about this case, or tell Ellis to act — e.g. "file the {primaryForm(c)} then submit it to {jurisdiction === 'Canada' ? 'IRCC' : 'USCIS'} on their behalf".</p>

      {submitFlow && (
        <PortalModal jurisdiction={jurisdiction} c={c} mode="submit" formName={submitFlow.formName}
          onSubmitted={onSubmitted} onClose={() => setSubmitFlow(null)} />
      )}

      <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask()} placeholder="Ask a question or give a command..." />
        <button className="btn" onClick={() => ask()} disabled={loading || !q.trim()}>{loading ? <span className="spinner" /> : 'Send'}</button>
      </div>

      {msgs.length === 0 && !loading && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {SUGGESTED.map((s) => <button key={s} className="chip" style={{ cursor: 'pointer' }} onClick={() => ask(s)}>{s}</button>)}
        </div>
      )}

      <ErrorNote error={error} onSettings={onSettings} />
      {loading && <Loading label="Ellis is reading the case" />}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 8 }}>
        {[...msgs].reverse().map((m) => (
          <div key={m.id} className="card" style={{ padding: 18 }}>
            <div style={{ fontWeight: 600, marginBottom: 10 }}>{m.question}</div>
            {m.ran && <div style={{ marginBottom: 10 }}><span className="chip"><Icon.spark style={{ width: 12, height: 12 }} /> Ellis ran: {m.ran}</span></div>}
            <p className="prose" style={{ whiteSpace: 'pre-wrap' }}>{m.answer}</p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              {m.download && <AskDownload dl={m.download} c={c} />}
              {m.reveal && <button className="btn btn--ghost btn--sm" style={{ marginTop: 12 }} onClick={() => ellis.revealFile(m.reveal)}><Icon.folder style={{ width: 14, height: 14 }} /> Show in Finder</button>}
            </div>
            {(m.citations || []).length > 0 && (
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>Sources</div>
                {m.citations.map((ct, i) => (
                  <div key={i} style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 4 }}>
                    <strong style={{ color: 'var(--ink)' }}>{ct.source}:</strong> {ct.detail}
                  </div>
                ))}
              </div>
            )}
            {(m.followUps || []).length > 0 && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                {m.followUps.map((f, i) => <button key={i} className="chip" style={{ cursor: 'pointer' }} onClick={() => ask(f)}>{f}</button>)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------------- Generic single-action capability ---------------- */
function RunTab({ c, cap, call, render, footer, onSettings }) {
  const meta = CAPABILITIES[cap]
  const toast = useToast()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)
  const [ranAt, setRanAt] = useState(null)

  async function run(manual) {
    setLoading(true); setError(null)
    // Small delay on manual re-run so the spinner is visible even though the
    // engine answers instantly — makes it obvious the action fired.
    if (manual) await new Promise((r) => setTimeout(r, 350))
    const res = await call()
    setLoading(false)
    if (!res.ok) return setError(res.error)
    setData(res.data)
    setRanAt(new Date())
    if (manual) toast(`Re-ran ${meta.label.toLowerCase()}`)
  }

  // Auto-run on open — no Run click required.
  useEffect(() => { run(false) /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 700 }}>{meta.label}</h2>
          <p style={{ color: 'var(--muted)', fontSize: 14, maxWidth: 460 }}>{meta.desc}</p>
        </div>
        <div style={{ textAlign: 'right' }}>
          <button className="btn btn--ghost" data-tour="run-btn" onClick={() => run(true)} disabled={loading}>{loading ? <><span className="spinner spinner--ink" /> Working</> : <><Icon.spark style={{ width: 16, height: 16 }} /> Re-run</>}</button>
          {ranAt && !loading && <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 6 }}>Updated {ranAt.toLocaleTimeString()}</div>}
        </div>
      </div>
      <ErrorNote error={error} onSettings={onSettings} />
      {loading && <Loading label={`Ellis is running ${meta.label.toLowerCase()}`} />}
      {data && <div className="result">{render(data)}</div>}
      {data && footer && footer(data)}
    </div>
  )
}

function PdfButton({ onClick }) {
  const [busy, setBusy] = useState(false)
  return (
    <button className="btn btn--ghost" style={{ marginTop: 16 }} onClick={async () => { setBusy(true); await onClick(); setBusy(false) }} disabled={busy}>
      {busy ? <><span className="spinner spinner--ink" /> Preparing</> : <><Icon.doc style={{ width: 16, height: 16 }} /> Download PDF</>}
    </button>
  )
}

/* ---------------- Notices ---------------- */
function retrievedNotice(c) {
  const today = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  if (c.destinationCountry === 'Canada') {
    return `IMMIGRATION, REFUGEES AND CITIZENSHIP CANADA (IRCC)
Date: ${today}
Application: ${c.pathway === 'student' ? 'Study Permit' : c.pathway === 'work' ? 'Work Permit' : 'Temporary Resident Visa'}
Applicant: ${c.applicantName}

Dear Applicant,

We are processing your application. Before we can continue, we require the following:
1. Biometrics — please attend a Visa Application Centre within 30 days of this letter.
2. Updated proof of funds covering tuition and living expenses for one year.
3. A certified English or French translation of any document not in English/French.

You must respond by ${new Date(Date.now() + 30 * 864e5).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}. Failure to respond may result in refusal of your application.

Sincerely,
IRCC Case Processing Centre`
  }
  return `U.S. CITIZENSHIP AND IMMIGRATION SERVICES
REQUEST FOR EVIDENCE (Form I-797E)
Date: ${today}
Case Type: ${c.pathway === 'work' ? 'I-129, Petition for a Nonimmigrant Worker' : c.pathway === 'student' ? 'I-539 / SEVIS' : 'I-539'}
Beneficiary: ${c.applicantName}

USCIS requires additional evidence to continue processing this petition:
1. Evidence that the position qualifies as a specialty occupation (detailed job duties and minimum degree requirement).
2. Evidence of the beneficiary's qualifying degree and any required evaluation.
3. A copy of the certified Labor Condition Application (LCA) for the offered wage and worksite.

You must submit all requested evidence by ${new Date(Date.now() + 60 * 864e5).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}. Failure to respond may result in denial.`
}

function NoticesTab({ c, onSettings }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [retrieving, setRetrieving] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  async function run(t) {
    const body = t ?? text
    if (!body.trim()) return
    setLoading(true); setError(null)
    const res = await ellis.ai.summarizeNotice({ text: body, case: c })
    setLoading(false)
    if (!res.ok) return setError(res.error)
    setData(res.data)
  }

  async function retrieve() {
    setRetrieving(true); setError(null)
    toast(`Signing in to ${c.destinationCountry === 'Canada' ? 'IRCC' : 'USCIS'} portal…`)
    await new Promise((r) => setTimeout(r, 1400))
    const notice = retrievedNotice(c)
    setText(notice)
    setRetrieving(false)
    toast('Notice retrieved from the portal')
    run(notice)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 700 }}>Notices / RFEs</h2>
          <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 16, maxWidth: 460 }}>Let Ellis sign in to the government portal and pull notices automatically, or paste one. Ellis turns it into clear deadlines and actions.</p>
        </div>
        <button className="btn" onClick={retrieve} disabled={retrieving}>{retrieving ? <><span className="spinner" /> Connecting</> : <><Icon.plug style={{ width: 16, height: 16 }} /> Sign in to {c.destinationCountry === 'Canada' ? 'IRCC' : 'USCIS'} & retrieve</>}</button>
      </div>
      <textarea className="textarea" style={{ minHeight: 150 }} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the notice text here, or use “Sign in & retrieve” above..." />
      <button className="btn btn--ghost" style={{ marginTop: 12 }} onClick={() => run()} disabled={loading || !text.trim()}>{loading ? <><span className="spinner spinner--ink" /> Summarizing</> : 'Summarize notice'}</button>
      <ErrorNote error={error} onSettings={onSettings} />
      {loading && <Loading />}
      {data && (
        <div className="result">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <h3>{data.noticeType}</h3><Sev level={data.severity} />
          </div>
          <p className="prose">{data.summary}</p>
          {(data.deadlines || []).length > 0 && (
            <div style={{ marginTop: 16 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Deadlines</div>
              <KVList fields={data.deadlines.map((d) => ({ label: d.label, value: d.date }))} />
            </div>
          )}
          {(data.requiredActions || []).length > 0 && (
            <div style={{ marginTop: 16 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Required actions</div>
              {data.requiredActions.map((a, i) => (
                <div key={i} className="row"><div className="row__main"><div className="row__title" style={{ fontWeight: 500 }}>{a.action}</div></div><span className="chip">{a.owner}</span></div>
              ))}
            </div>
          )}
          {(data.evidenceRequested || []).length > 0 && (
            <div style={{ marginTop: 16 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Evidence requested</div><Checklist items={data.evidenceRequested} /></div>
          )}
        </div>
      )}
    </div>
  )
}

/* ---------------- Forms ---------------- */
function FormsTab({ c, role, onSettings, onConnect }) {
  const suggestions = (VISA_TYPES[c.destinationCountry]?.[c.pathway]) || []
  const [formType, setFormType] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  async function run(ft) {
    const type = ft || formType
    if (!type.trim()) return
    setLoading(true); setError(null)
    // Brief processing pause so the user sees Ellis populating the form.
    await new Promise((r) => setTimeout(r, 900))
    const res = await ellis.ai.prepareForm({ formType: type, case: c })
    setLoading(false)
    if (!res.ok) return setError(res.error)
    setData(res.data)
  }

  // Auto-prepare the most likely form on open — no Run click required.
  useEffect(() => { const f = primaryForm(c); setFormType(f); run(f) /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const COMMON = c.destinationCountry === 'Canada'
    ? ['IMM 1295 (Work Permit)', 'IMM 1294 (Study Permit)', 'IMM 5257 (Visitor Visa)']
    : ['Form I-129', 'Form I-539', 'Form DS-160', 'Form I-765 (EAD)']

  return (
    <div>
      <h2 style={{ fontSize: 20, fontWeight: 700 }}>Form preparation</h2>
      <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 16 }}>Ellis pre-fills a form from the case facts and marks everything still missing.</p>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
        <input className="input" value={formType} onChange={(e) => setFormType(e.target.value)} placeholder="Form name or number, e.g. I-129 or IMM 1295" />
        <button className="btn" onClick={() => run()} disabled={loading || !formType.trim()}>{loading ? <span className="spinner" /> : 'Prepare'}</button>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
        {COMMON.map((f) => <button key={f} className="chip" style={{ cursor: 'pointer' }} onClick={() => { setFormType(f); run(f) }}>{f}</button>)}
      </div>

      <ErrorNote error={error} onSettings={onSettings} />
      {loading && <Loading />}
      {data && (
        <div className="result">
          <h3>{data.formName}</h3>
          <p className="prose" style={{ marginBottom: 4 }}>{data.purpose}</p>
          <span className="chip">{data.jurisdiction}</span>
          {(data.sections || []).map((s, i) => (
            <div key={i} style={{ marginTop: 18 }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>{s.title}</div>
              <KVList fields={s.fields} />
            </div>
          ))}
          {(data.missingInfo || []).length > 0 && (
            <div style={{ marginTop: 18 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Still needed</div><Checklist items={data.missingInfo} /></div>
          )}
          {(data.filingChecklist || []).length > 0 && (
            <div style={{ marginTop: 18 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Filing checklist</div><Checklist items={data.filingChecklist} /></div>
          )}
        </div>
      )}
      {data && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <PdfButton onClick={() => downloadFormPdf(data, c)} />
          {role !== 'counsel' && (
            <button className="btn" style={{ marginTop: 16 }} onClick={onConnect}>
              <Icon.plug style={{ width: 16, height: 16 }} /> Connect to {(GOV_PORTAL[c.destinationCountry] || GOV_PORTAL.Canada).label} & file
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/* ---------------- Evidence packet ---------------- */
function EvidenceTab({ c, role, onSettings, onNotify }) {
  const toast = useToast()
  return (
    <RunTab c={c} cap="evidence" onSettings={onSettings} call={() => ellis.ai.evidencePacket({ case: c })}
      footer={(d) => (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <PdfButton onClick={() => downloadHandoffPdf(d, c)} />
          {role !== 'counsel' && <button className="btn btn--ghost" style={{ marginTop: 16 }} onClick={() => notifyCounsel({ c, role, toast, onNotify })}><Icon.mail style={{ width: 16, height: 16 }} /> Email packet to counsel</button>}
        </div>
      )}
      render={(d) => (
      <div>
        <h3>{d.title}</h3>
        <p className="prose" style={{ margin: '6px 0 4px' }}>{d.overview}</p>
        {d.recommendedPathway && <span className="chip">{d.recommendedPathway}</span>}
        {(d.exhibits || []).length > 0 && (
          <div style={{ marginTop: 18 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Exhibits</div>
            {d.exhibits.map((e, i) => (
              <div key={i} className="row"><div className="row__main"><div className="row__title">{e.label}</div><div className="row__sub">{e.description}</div></div><span className="chip">{e.status}</span></div>
            ))}
          </div>
        )}
        {(d.openItems || []).length > 0 && (
          <div style={{ marginTop: 18 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Open items for counsel</div><Checklist items={d.openItems} /></div>
        )}
        {d.attorneyNotes && (
          <div className="card card--soft" style={{ padding: 16, marginTop: 18 }}>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Attorney notes</div>
            <p className="prose">{d.attorneyNotes}</p>
          </div>
        )}
      </div>
    )} />
  )
}

/* ---------------- Compliance (audit + risk flags in one scan) ---------------- */
function ComplianceTab({ c, onSettings }) {
  const toast = useToast()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)
  const [ranAt, setRanAt] = useState(null)

  async function run(manual) {
    setLoading(true); setError(null)
    if (manual) await new Promise((r) => setTimeout(r, 350))
    const [audit, risks] = await Promise.all([
      ellis.ai.complianceAudit({ case: c }),
      ellis.ai.riskFlags({ case: c })
    ])
    setLoading(false)
    if (!audit.ok) return setError(audit.error)
    if (!risks.ok) return setError(risks.error)
    // Merge findings from both scans. The audit and risk scans often describe
    // the same issue with different titles, so dedupe on the substance
    // (explanation + next action) rather than the headline.
    const seen = new Set()
    const findings = [...(risks.data.findings || []), ...(audit.data.findings || [])].filter((f) => {
      const k = ((f.explanation || f.issue || '') + '|' + (f.nextAction || f.remediation || '')).toLowerCase().replace(/[^a-z0-9|]/g, '')
      const kt = (f.title || f.issue || f.area || '').toLowerCase()
      if ((!k.replace('|', '') && !kt) || seen.has(k) || seen.has(kt)) return false
      seen.add(k); seen.add(kt)
      return true
    })
    setData({ score: audit.data.score, summary: audit.data.summary, passed: audit.data.passed || [], findings })
    setRanAt(new Date())
    if (manual) toast('Compliance scan re-run')
  }

  useEffect(() => { run(false) /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const issues = (data?.findings || []).filter((f) => (f.severity || '').toLowerCase() !== 'info')
  const infos = (data?.findings || []).filter((f) => (f.severity || '').toLowerCase() === 'info')

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 700 }}>Compliance</h2>
          <p style={{ color: 'var(--muted)', fontSize: 14, maxWidth: 460 }}>One scan covering the compliance audit and every risk flag on this case.</p>
        </div>
        <div style={{ textAlign: 'right' }}>
          <button className="btn btn--ghost" data-tour="run-btn" onClick={() => run(true)} disabled={loading}>{loading ? <><span className="spinner spinner--ink" /> Scanning</> : <><Icon.spark style={{ width: 16, height: 16 }} /> Re-run scan</>}</button>
          {ranAt && !loading && <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 6 }}>Updated {ranAt.toLocaleTimeString()}</div>}
        </div>
      </div>

      <ErrorNote error={error} onSettings={onSettings} />
      {loading && <Loading label="Ellis is scanning the case" />}

      {data && !loading && (
        <>
          {/* Score header: donut + verdict, calm and simple */}
          <div className="card" style={{ padding: '22px 26px', marginBottom: 16, display: 'flex', gap: 26, alignItems: 'center', flexWrap: 'wrap' }}>
            <AnimatedDonut value={data.score} size={116} />
            <div style={{ flex: 1, minWidth: 260 }}>
              <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>
                {data.score >= 85 ? 'In good standing' : data.score >= 70 ? 'Needs attention' : 'At risk'}
              </div>
              <p className="prose" style={{ fontSize: 13.5, marginBottom: 10 }}>{data.summary}</p>
              <div style={{ display: 'flex', gap: 16, fontSize: 12.5, color: 'var(--muted)' }}>
                <span><strong style={{ color: 'var(--ink)' }}>{issues.length}</strong> to address</span>
                <span><strong style={{ color: 'var(--ink)' }}>{(data.passed || []).length}</strong> checks passing</span>
              </div>
            </div>
          </div>

          {issues.length > 0 && (
            <>
              <div className="eyebrow" style={{ marginBottom: 10 }}>Needs attention</div>
              <Findings items={issues} />
            </>
          )}
          {issues.length === 0 && (
            <div className="card card--soft" style={{ padding: 16, fontSize: 13.5, color: 'var(--muted)', marginBottom: 16 }}>
              No blocking risks — the case is clear to advance.
            </div>
          )}

          {(data.passed || []).length > 0 && (
            <div style={{ marginTop: 18 }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>Passing checks</div>
              <Checklist items={(data.passed || []).map((p) => ({ item: p, status: 'ready' }))} />
            </div>
          )}
          {infos.length > 0 && (
            <div style={{ marginTop: 18 }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>For awareness</div>
              <Checklist items={infos.map((f) => ({ item: f.title || f.issue, status: 'info' }))} />
            </div>
          )}
        </>
      )}
    </div>
  )
}

/* ---------------- Travel ---------------- */
function TravelTab({ c, onSettings }) {
  const [trip, setTrip] = useState({ destination: '', departure: '', return: '', purpose: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  async function run() {
    setLoading(true); setError(null)
    const res = await ellis.ai.travelRisk({ trip, case: c })
    setLoading(false)
    if (!res.ok) return setError(res.error)
    setData(res.data)
  }

  // Auto-run a baseline assessment on open; refine by entering a specific trip.
  useEffect(() => { run() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const recColor = { go: '#0a0a0a', caution: '#6b6b6b', hold: '#0a0a0a' }

  return (
    <div>
      <h2 style={{ fontSize: 20, fontWeight: 700 }}>Travel risk</h2>
      <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 16 }}>Assess a trip before anyone books it. Ellis weighs status, pending petitions, and re-entry exposure.</p>
      <div className="grid grid-2">
        <div className="field"><label>Destination</label><input className="input" value={trip.destination} onChange={(e) => setTrip({ ...trip, destination: e.target.value })} placeholder="e.g. China, Toronto" /></div>
        <div className="field"><label>Purpose</label><input className="input" value={trip.purpose} onChange={(e) => setTrip({ ...trip, purpose: e.target.value })} placeholder="e.g. family visit" /></div>
        <div className="field"><label>Departure</label><input className="input" value={trip.departure} onChange={(e) => setTrip({ ...trip, departure: e.target.value })} placeholder="e.g. 2026-08-10" /></div>
        <div className="field"><label>Return</label><input className="input" value={trip.return} onChange={(e) => setTrip({ ...trip, return: e.target.value })} placeholder="e.g. 2026-08-24" /></div>
      </div>
      <button className="btn" onClick={run} disabled={loading}>{loading ? <><span className="spinner" /> Assessing</> : 'Assess trip'}</button>
      <ErrorNote error={error} onSettings={onSettings} />
      {loading && <Loading />}
      {data && (
        <div className="result">
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 10 }}>
            <span style={{ textTransform: 'uppercase', fontWeight: 700, letterSpacing: 1, padding: '6px 14px', borderRadius: 999, background: recColor[data.recommendation] || '#0a0a0a', color: '#fff' }}>{data.recommendation}</span>
            <span className="chip">Re-entry risk: {data.reentryRisk}</span>
          </div>
          <p className="prose" style={{ marginBottom: 14 }}>{data.summary}</p>
          {(data.reasons || []).length > 0 && (
            <div style={{ marginBottom: 14 }}><div className="eyebrow" style={{ marginBottom: 8 }}>Why</div>
              <ul style={{ paddingLeft: 18 }}>{data.reasons.map((r, i) => <li key={i} className="prose" style={{ marginBottom: 4 }}>{r}</li>)}</ul>
            </div>
          )}
          {(data.checklist || []).length > 0 && (<><div className="eyebrow" style={{ marginBottom: 8 }}>Before you go</div><Checklist items={data.checklist} /></>)}
        </div>
      )}
    </div>
  )
}

/* ---------------- Document picker (used by authenticity) ---------------- */
function DocSource({ c, text, setText }) {
  return (
    <div style={{ marginBottom: 12 }}>
      {(c.documents || []).length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          {c.documents.map((d) => (
            <button key={d.id} className="chip" style={{ cursor: 'pointer' }} onClick={() => setText(d.text || '')}>{d.name}</button>
          ))}
        </div>
      )}
      <textarea className="textarea" style={{ minHeight: 130 }} value={text} onChange={(e) => setText(e.target.value)} placeholder="Pick a document above or paste text here..." />
    </div>
  )
}

/* ---------------- Authenticity ---------------- */
function AuthenticityTab({ c, onSettings }) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  async function run(t) {
    const body = t ?? text
    if (!body.trim()) return
    setLoading(true); setError(null)
    const res = await ellis.ai.authenticityCheck({ text: body })
    setLoading(false)
    if (!res.ok) return setError(res.error)
    setData(res.data)
  }

  // Auto-screen the first document on open.
  useEffect(() => {
    const doc = (c.documents || [])[0]
    if (doc?.text) { setText(doc.text); run(doc.text) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div>
      <h2 style={{ fontSize: 20, fontWeight: 700 }}>Document authenticity</h2>
      <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 16 }}>Automated screening for consistency, identifiers, and specimen markers before a document enters the filing.</p>
      <DocSource c={c} text={text} setText={setText} />
      <button className="btn" onClick={run} disabled={loading || !text.trim()}>{loading ? <><span className="spinner" /> Screening</> : 'Screen document'}</button>
      <ErrorNote error={error} onSettings={onSettings} />
      {loading && <Loading />}
      {data && (
        <div className="result">
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 14 }}>
            <div style={{ fontSize: 44, fontWeight: 700, lineHeight: 1 }}>{data.score}</div>
            <div>
              <span style={{ textTransform: 'uppercase', fontWeight: 700, letterSpacing: 1, fontSize: 12 }}>{data.verdict}</span>
              <p className="prose" style={{ fontSize: 13.5 }}>{data.summary}</p>
            </div>
          </div>
          {(data.checks || []).map((ck, i) => (
            <div key={i} className="row"><Sev level={ck.status === 'pass' ? 'low' : 'high'} /><div className="row__main"><div className="row__title" style={{ fontWeight: 500 }}>{ck.label}</div><div className="row__sub">{ck.detail}</div></div><span className="chip">{ck.status}</span></div>
          ))}
          {data.disclaimer && <div className="card card--soft" style={{ padding: 14, marginTop: 14, fontSize: 13 }}>{data.disclaimer}</div>}
        </div>
      )}
    </div>
  )
}
