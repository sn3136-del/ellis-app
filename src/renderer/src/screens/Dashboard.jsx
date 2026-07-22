import { useState, useEffect, useRef } from 'react'
import { ellis, ROLE_TILES, CAPABILITIES, fmtDate, notifyOthers, filterCasesForProfile } from '../lib/api.js'
import { downloadHandoffPdf } from '../lib/pdf.js'
import { Icon } from '../components/icons.jsx'
import { Empty, useToast } from '../components/ui.jsx'
import PortalModal from '../components/PortalModal.jsx'
import { ToolsGrid } from '../components/Integrations.jsx'
import { useCountUp, useMounted, AnimatedDonut, StageBar, caseProgress } from '../components/charts.jsx'

const TILE_ICON = {
  qa: 'chat', documents: 'doc', notices: 'bell', travel: 'plane',
  compliance: 'gauge', risks: 'shield', evidence: 'folder', forms: 'form',
  translation: 'flow', authenticity: 'shield'
}

function monthsUntil(s) { const d = Date.parse(s); return isNaN(d) ? null : Math.round((d - Date.now()) / 2.628e9) }

// Derive a meaningful compliance score (0-100) from real case data.
function compliance(c) {
  let score = 100
  const f = c.facts || {}
  if (c.pathway === 'work' && !c.employer) score -= 18
  const nd = (c.documents || []).length
  if (nd < 2) score -= 28; else if (nd < 3) score -= 12
  const pe = monthsUntil(f['Passport Expiry'])
  if (pe !== null && pe <= 6) score -= 16
  const se = monthsUntil(f['Status Valid Until'])
  if (se !== null && se <= 4) score -= 18
  const openHigh = (c.tasks || []).filter((t) => t.status !== 'done' && t.priority === 'high').length
  score -= Math.min(18, openHigh * 9)
  return Math.max(35, Math.min(100, Math.round(score)))
}

/* ---------------- Employer: one animated operations graph ---------------- */
function WorkforceGraph({ cases, openTasks, docs, onOpenCase, onJump }) {
  const mounted = useMounted()
  const nCases = useCountUp(cases.length)
  const nTasks = useCountUp(openTasks)
  const nDocs = useCountUp(docs)
  if (!cases.length) return null

  const data = cases.map((c) => ({ c, score: compliance(c) })).sort((a, b) => a.score - b.score)
  const avg = Math.round(data.reduce((n, d) => n + d.score, 0) / data.length)
  const atRisk = data.filter((d) => d.score < 75).length
  const band = (s) => s >= 85 ? 'Compliant' : s >= 75 ? 'Watch' : 'At risk'

  const Stat = ({ n, cap, jump }) => (
    <button onClick={jump} style={{ background: 'none', border: 'none', padding: '2px 22px', cursor: 'pointer', font: 'inherit', textAlign: 'left', borderLeft: '1px solid var(--line)' }}>
      <div style={{ fontSize: 32, fontWeight: 800, lineHeight: 1, letterSpacing: -0.5 }}>{n}</div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6, display: 'flex', alignItems: 'center', gap: 4 }}>
        {cap} <Icon.back style={{ width: 11, height: 11, transform: 'rotate(-90deg)', opacity: 0.45 }} />
      </div>
    </button>
  )

  return (
    <div className="card" style={{ padding: '24px 26px', marginBottom: 30 }} data-tour="emp-chart">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <div className="eyebrow">Workforce overview</div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>
            {atRisk ? <><strong style={{ color: 'var(--ink)' }}>{atRisk}</strong> of {data.length} cases need attention</> : 'All cases compliant'} · updated live
          </div>
        </div>
        <div style={{ display: 'flex' }}>
          <Stat n={nCases} cap="Active cases" jump={() => onJump('cases')} />
          <Stat n={nTasks} cap="Open tasks" jump={() => onJump('tasks')} />
          <Stat n={nDocs} cap="Documents" jump={() => onJump('docs')} />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 30, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, minWidth: 140 }}>
          <AnimatedDonut value={avg} />
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>avg compliance</div>
        </div>
        <div style={{ flex: 1, minWidth: 320, display: 'flex', flexDirection: 'column', gap: 11 }}>
          {data.map((d, i) => (
            <button key={d.c.id} onClick={() => onOpenCase(d.c.id)} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', textAlign: 'left', font: 'inherit' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{d.c.applicantName} <span style={{ color: 'var(--muted-2)', fontWeight: 400 }}>· {d.c.visaType || d.c.pathway}</span></span>
                <span style={{ fontSize: 11.5, color: d.score < 75 ? 'var(--ink)' : 'var(--muted)', fontWeight: d.score < 75 ? 700 : 400 }}>{band(d.score)} · {d.score}</span>
              </div>
              <div style={{ height: 8, background: 'rgba(10,10,10,0.06)', borderRadius: 999, overflow: 'hidden' }}>
                <div style={{
                  width: mounted ? `${d.score}%` : '0%', height: '100%', borderRadius: 999,
                  background: d.score < 75 ? 'repeating-linear-gradient(45deg,#9a9a9a,#9a9a9a 5px,#c4c4c4 5px,#c4c4c4 10px)' : '#0a0a0a',
                  transition: `width 1s cubic-bezier(.22,1,.36,1) ${i * 90}ms`
                }} />
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ---------------- Immigrant: single pending case, animated ---------------- */
function MyCaseCard({ c, onOpenCase }) {
  const tasks = c.tasks || []
  const done = tasks.filter((t) => t.status === 'done').length
  const pct = caseProgress(c)
  const nDocs = useCountUp((c.documents || []).length)
  const nOpen = useCountUp(tasks.length - done)
  const nextTask = tasks.find((t) => t.status !== 'done')

  return (
    <div className="card" style={{ padding: '24px 26px', marginBottom: 30 }} data-tour="emp-chart">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 14 }}>
        <div>
          <div className="eyebrow">Your case</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 6 }}>{c.applicantName}</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', marginTop: 2 }}>
            {c.originCountry || 'Origin'} → {c.destinationCountry} · {c.visaType || c.pathway}{c.employer ? ` · ${c.employer}` : ''}
          </div>
          <div style={{ display: 'flex', gap: 20, marginTop: 18 }}>
            <div><div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1 }}>{nDocs}</div><div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 4 }}>documents on file</div></div>
            <div style={{ borderLeft: '1px solid var(--line)', paddingLeft: 20 }}><div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1 }}>{nOpen}</div><div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 4 }}>tasks remaining</div></div>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
          <AnimatedDonut value={pct} size={124} sublabel="% complete" />
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>case progress</div>
        </div>
      </div>

      {/* Stage timeline */}
      <div style={{ marginTop: 22, marginBottom: 4 }}>
        <StageBar c={c} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 16, flexWrap: 'wrap', gap: 10 }}>
        <div style={{ fontSize: 13, color: 'var(--muted)' }}>
          {nextTask ? <>Next: <strong style={{ color: 'var(--ink)' }}>{nextTask.title}</strong>{nextTask.due ? ` · ${nextTask.due}` : ''}</> : 'No open tasks — Ellis is monitoring your case.'}
        </div>
        <button className="btn" data-tour="open-case" onClick={() => onOpenCase(c.id)}>Open my case <Icon.arrow style={{ width: 15, height: 15 }} /></button>
      </div>
    </div>
  )
}

function ImmigrantConnect({ jurisdiction, cases }) {
  const [portal, setPortal] = useState(false)
  const label = jurisdiction === 'Canada' ? 'IRCC' : 'USCIS'
  return (
    <div className="card card--ink" style={{ padding: 24, marginBottom: 30, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 14 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 700 }}>Let Ellis file & track for you</div>
        <p style={{ fontSize: 13.5, color: 'rgba(255,255,255,0.62)', marginTop: 4, maxWidth: 440 }}>
          Connect your {label} account once. Ellis signs in, completes your forms, submits the filing, and reads your live case status back to you.
        </p>
      </div>
      <button className="btn" style={{ background: '#fff', color: 'var(--ink)' }} onClick={() => setPortal(true)}>
        <Icon.plug style={{ width: 16, height: 16 }} /> Connect to {label}
      </button>
      {portal && <PortalModal jurisdiction={jurisdiction} c={cases?.[0]} onClose={() => setPortal(false)} />}
    </div>
  )
}

function EmployerConnect({ cases, onNotify, jurisdiction }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [portal, setPortal] = useState(false)
  const label = jurisdiction === 'Canada' ? 'IRCC' : 'USCIS'

  async function emailLawyer() {
    if (!cases.length) return toast('No cases to report on')
    setBusy(true)
    const c = [...cases].sort((a, b) => compliance(a) - compliance(b))[0]
    toast(`Preparing handoff for ${c.applicantName}…`)
    const res = await ellis.ai.evidencePacket({ case: c })
    if (res.ok) { try { await downloadHandoffPdf(res.data, c) } catch (e) {} }
    const subject = `Ellis case update — ${c.applicantName} (${c.originCountry || ''} → ${c.destinationCountry})`
    const body = [
      'Hi counsel,', '',
      `Sharing the latest on ${c.applicantName}'s ${c.pathway} case${c.visaType ? ` (${c.visaType})` : ''}.`,
      'The counsel handoff packet (PDF) has been generated and saved for your review.', '',
      'Open the full case in Ellis (documents, risks, evidence index):',
      `Ellis → Cases → ${c.applicantName}  (sign in: admin / 1234)`, '',
      '— Sent automatically from Ellis'
    ].join('\n')
    try { await ellis.openExternal(`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`) } catch (e) {}
    await notifyOthers('employer', c, `Counsel notified — handoff packet ready for ${c.applicantName}`, ['counsel'])
    onNotify?.()
    setBusy(false)
    toast('Counsel emailed · packet drafted & attached')
  }

  return (
    <div className="card" style={{ padding: 22, marginBottom: 30 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12, marginBottom: 18 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Connect your tools</div>
          <p style={{ fontSize: 13.5, color: 'var(--muted)', maxWidth: 420 }}>Click a tool to sign in. Ellis pulls case context from the tools your team already uses.</p>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button className="btn" onClick={() => setPortal(true)}><Icon.plug style={{ width: 16, height: 16 }} /> Connect to {label} & file</button>
          <button className="btn btn--ghost" onClick={emailLawyer} disabled={busy}>{busy ? <><span className="spinner spinner--ink" /> Sending</> : <><Icon.mail style={{ width: 16, height: 16 }} /> Email lawyer an update</>}</button>
        </div>
      </div>
      {portal && <PortalModal jurisdiction={jurisdiction} c={cases?.[0]} onClose={() => setPortal(false)} />}
      <ToolsGrid />
    </div>
  )
}

/* ---------------- What Ellis can do — simple action rows ---------------- */
function CapabilityRows({ role, onNav }) {
  const tiles = ROLE_TILES[role] || []
  return (
    <div className="card" style={{ padding: '8px 0', marginBottom: 30 }} data-tour="tiles">
      <div className="eyebrow" style={{ padding: '14px 20px 6px' }}>What Ellis can do for you</div>
      {tiles.map((cap, i) => {
        const Ico = Icon[TILE_ICON[cap]]
        return (
          <button key={cap} onClick={() => onNav('cases')}
            style={{ display: 'flex', alignItems: 'center', gap: 14, width: '100%', padding: '13px 20px', background: 'none', border: 'none', borderTop: i ? '1px solid var(--line)' : 'none', cursor: 'pointer', font: 'inherit', textAlign: 'left', transition: 'background .12s' }}
            onMouseEnter={(e) => e.currentTarget.style.background = 'var(--bg-soft)'}
            onMouseLeave={(e) => e.currentTarget.style.background = ''}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--ink)', color: '#fff', display: 'grid', placeItems: 'center', flexShrink: 0 }}>
              <Ico style={{ width: 16, height: 16 }} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{CAPABILITIES[cap].label}</div>
              <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{CAPABILITIES[cap].desc}</div>
            </div>
            <Icon.arrow style={{ width: 15, height: 15, opacity: 0.4, flexShrink: 0 }} />
          </button>
        )
      })}
    </div>
  )
}

/* ---------------- Dashboard ---------------- */
export default function Dashboard({ role, profile, onNav, onOpenCase, onNotify }) {
  const [cases, setCases] = useState([])
  const jurisdiction = profile?.jurisdiction || 'USA'
  const recentRef = useRef(null)
  const tasksRef = useRef(null)
  const docsRef = useRef(null)

  useEffect(() => {
    ellis.listCases().then((all) => setCases(filterCasesForProfile(all, role, profile)))
  }, [role, profile])

  const openTasksList = cases.flatMap((c) => (c.tasks || []).filter((t) => t.status !== 'done').map((t) => ({ t, c })))
  const docsList = cases.flatMap((c) => (c.documents || []).map((d) => ({ d, c })))
  const scrollTo = (ref) => ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const jump = (k) => scrollTo(k === 'cases' ? recentRef : k === 'tasks' ? tasksRef : docsRef)

  // An immigrant runs one pending case at a time — most recently updated wins.
  const myCase = role === 'immigrant' && cases.length ? [...cases].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))[0] : null
  const otherCases = myCase ? cases.filter((c) => c.id !== myCase.id) : cases
  const listCases = role === 'immigrant' ? otherCases : cases
  const shownTasks = role === 'immigrant' && myCase ? openTasksList.filter((x) => x.c.id === myCase.id) : openTasksList
  const shownDocs = role === 'immigrant' && myCase ? docsList.filter((x) => x.c.id === myCase.id) : docsList

  return (
    <div>
      <div className="topbar">
        <h1>Home</h1>
        <div className="topbar__actions">
          <button className="btn btn--ghost" onClick={() => onNav('cases')}>All cases</button>
          <button className="btn" onClick={() => onNav('cases')}><Icon.plus style={{ width: 16, height: 16 }} /> New case</button>
        </div>
      </div>

      <div className="page page--wide">
        {role === 'counsel' && (
          <div className="grid grid-3" style={{ marginTop: 4, marginBottom: 28 }}>
            {[[cases.length, 'Active cases', recentRef], [openTasksList.length, 'Open tasks', tasksRef], [docsList.length, 'Documents processed', docsRef]].map(([n, cap, ref]) => (
              <button key={cap} className="card stat" onClick={() => scrollTo(ref)} style={{ cursor: 'pointer', textAlign: 'left', font: 'inherit', border: '1px solid var(--line)' }}>
                <div className="stat__num">{n}</div>
                <div className="stat__cap" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>{cap} <Icon.back style={{ width: 12, height: 12, transform: 'rotate(-90deg)', opacity: 0.5 }} /></div>
              </button>
            ))}
          </div>
        )}

        {role === 'employer' && (
          <WorkforceGraph cases={cases} openTasks={openTasksList.length} docs={docsList.length} onOpenCase={onOpenCase} onJump={jump} />
        )}
        {role === 'immigrant' && myCase && <MyCaseCard c={myCase} onOpenCase={onOpenCase} />}
        {role === 'immigrant' && !myCase && (
          <Empty title="No case yet" sub="Create your case and Ellis will run it end to end.">
            <button className="btn" onClick={() => onNav('cases')}>Start my case</button>
          </Empty>
        )}

        {role === 'immigrant' && <ImmigrantConnect jurisdiction={jurisdiction} cases={myCase ? [myCase] : cases} />}
        {role === 'employer' && <EmployerConnect cases={cases} onNotify={onNotify} jurisdiction={jurisdiction} />}

        {role !== 'counsel' && <CapabilityRows role={role} onNav={onNav} />}

        <div ref={recentRef} className="eyebrow" style={{ marginBottom: 12, scrollMarginTop: 16 }}>
          {role === 'immigrant' ? 'Other cases on your profile' : 'Recent cases'}
        </div>
        {listCases.length === 0
          ? (role === 'immigrant'
            ? <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 20 }}>None — you have one active case, which is exactly right. Immigrants run one case at a time.</p>
            : <Empty title="No cases yet" sub="Create your first case to get started."><button className="btn" onClick={() => onNav('cases')}>Create a case</button></Empty>)
          : listCases.slice(0, 6).map((c, idx) => (
            <button key={c.id} data-tour={role !== 'immigrant' && idx === 0 ? 'open-case' : undefined} className="row" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }} onClick={() => onOpenCase(c.id)}>
              <div className="captile__icon" style={{ width: 36, height: 36 }}><Icon.folder style={{ width: 17, height: 17 }} /></div>
              <div className="row__main">
                <div className="row__title">{c.applicantName}</div>
                <div className="row__sub">{c.originCountry || 'Origin'} → {c.destinationCountry} · {c.pathway}</div>
              </div>
              <span className="chip">{c.facts?.stage || 'New'}</span>
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>{fmtDate(c.updatedAt)}</span>
            </button>
          ))}

        <div ref={tasksRef} className="eyebrow" style={{ marginTop: 30, marginBottom: 12, scrollMarginTop: 16 }}>Open tasks</div>
        {shownTasks.length === 0
          ? <Empty title="No open tasks" sub="Every task across your cases is complete." />
          : shownTasks.slice(0, 8).map(({ t, c }) => (
            <button key={c.id + t.id} className="row" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }} onClick={() => onOpenCase(c.id)}>
              <div className="captile__icon" style={{ width: 36, height: 36 }}><Icon.flow style={{ width: 16, height: 16 }} /></div>
              <div className="row__main">
                <div className="row__title">{t.title}</div>
                <div className="row__sub">{c.applicantName} · {t.owner}{t.due ? ` · due ${t.due}` : ''}</div>
              </div>
              {t.priority && <span className="chip">{t.priority}</span>}
            </button>
          ))}

        <div ref={docsRef} className="eyebrow" style={{ marginTop: 30, marginBottom: 12, scrollMarginTop: 16 }}>Documents processed</div>
        {shownDocs.length === 0
          ? <Empty title="No documents yet" sub="Add documents to a case to see them here." />
          : shownDocs.slice(0, 8).map(({ d, c }) => (
            <button key={c.id + d.id} className="row" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }} onClick={() => onOpenCase(c.id)}>
              <div className="captile__icon" style={{ width: 36, height: 36 }}><Icon.doc style={{ width: 16, height: 16 }} /></div>
              <div className="row__main">
                <div className="row__title">{d.name}</div>
                <div className="row__sub">{c.applicantName} · {d.extracted ? `${d.extracted.docType || 'Reviewed'}` : 'Not reviewed'}</div>
              </div>
              {d.extracted ? <Icon.check style={{ width: 16, height: 16 }} /> : <span className="chip">new</span>}
            </button>
          ))}
      </div>
    </div>
  )
}
