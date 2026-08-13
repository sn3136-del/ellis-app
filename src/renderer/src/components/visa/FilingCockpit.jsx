// The filing cockpit — docs/MAX_AUTOMATION_SPEC.md's "one screen per filing".
//
// It shows three things and nothing else:
//   1. Everything Ellis has prepared — every field it filled, each traceable to
//      its source; the computed wage level; the assembled documents; the
//      drafted narrative.
//   2. What is still missing — honestly, each with the ONE input needed.
//   3. THE SINGLE ACTION — "Open secure window and finish", which opens the
//      applicant's OWN session already driven to the point of their act.
//
// The action opens a window. It does not log in, sign, pay, submit, or clear a
// human check, and the cockpit says so out loud: the taps-to-done target and
// the named human acts sit beside the button, never behind it. That is not a
// disclaimer bolted on — the runtime has no path to those acts, and this
// surface must not imply otherwise.
//
// Shape-neutral by design: it renders a prepared government form (ETA-9035,
// ETA-9141, I-129), a PAF manifest, or a tourist consular form through the same
// display model. Only the loader differs.
import { useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { ErrorNote } from '../ui.jsx'
import { filingCockpitView, HUMAN_ACT_KEYS } from '../../lib/visaBackend.js'
import { usePortalLiveView, LiveFrame } from './handoffs.jsx'

// US dollars, no cents (mirrors EmployerConsole's money()).
function money(n) {
  return typeof n === 'number' && Number.isFinite(n)
    ? '$' + Math.round(n).toLocaleString('en-US') : ''
}

// One named human act. A backend-supplied act renders its own words; a curated
// one localizes. Either way it names a person, never Ellis.
function HumanAct({ t, act }) {
  const known = act.key && HUMAN_ACT_KEYS.includes(act.key)
  const label = known ? t(`cockpit.act.${act.key}`) : act.act
  if (!label) return null
  return (
    <li>
      {label}
      {act.who && <span style={{ color: 'var(--muted)' }}> · {t('cockpit.acts.who', { who: act.who })}</span>}
      {act.nonDelegable === true && (
        <span style={{ color: '#c77700' }}> · {t('cockpit.acts.nonDelegable')}</span>
      )}
      {act.ellisDoes && (
        <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>
          {t('cockpit.acts.ellis', { what: act.ellisDoes })}
        </div>
      )}
    </li>
  )
}

// The applicant's own secure session, mounted only after the single action is
// tapped. Interactive: this is the surface where the human performs their act.
function SecureWindow({ t, client, caseId, onClose }) {
  const view = usePortalLiveView(client, caseId)
  return (
    <div style={{ marginTop: 10 }} data-testid="cockpit-secure-window">
      {view.state === 'connecting' && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('cockpit.action.opening')}</div>
      )}
      <LiveFrame view={view} height="60vh" client={client} caseId={caseId} />
      <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 6 }}>
        {t('cockpit.action.note')}
      </div>
      {/* Closing ends the isolated session server-side, the same way every
          other secure-window surface does — a cloud browser is never left
          running behind a collapsed panel. */}
      <button className="btn btn--sm btn--ghost" style={{ marginTop: 8 }}
              onClick={async () => { try { await view.close() } finally { onClose() } }}>
        {t('cockpit.action.close')}
      </button>
    </div>
  )
}

export default function FilingCockpit({
  client, caseId, sessionCaseId = '', formKey = '', kind = 'form',
  routeKey = '', title = '', load = null
}) {
  const { t, lang } = useLocale()
  const [view, setView] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [windowOpen, setWindowOpen] = useState(false)

  function defaultLoad() {
    if (kind === 'paf') return client.h1bPafManifest(caseId, lang)
    if (kind === 'consular') return client.consularForm(caseId)
    if (formKey === 'eta-9141') return client.prepareEta9141(caseId, lang)
    // A form cockpit with no form key would post to a half-built path and get
    // a 404 the applicant cannot act on. Say what is actually wrong instead.
    if (!formKey) return Promise.reject(new Error(t('cockpit.noForm')))
    return client.h1bPrepareForm(caseId, formKey, lang)
  }

  async function refresh() {
    setBusy(true); setError(null)
    try {
      const payload = await (load ? load() : defaultLoad())
      setView(filingCockpitView(payload, {
        formKey, routeKey: routeKey || formKey || kind, sessionCaseId
      }))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 16 }} data-testid="filing-cockpit">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
                    alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 13.5 }}>{title || t('cockpit.title')}</div>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>{t('cockpit.sub')}</div>
        </div>
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={refresh}
                data-testid="cockpit-load">
          {busy ? t('cockpit.loading') : view ? t('cockpit.refresh') : t('cockpit.load')}
        </button>
      </div>
      {/* Standing honesty: nothing on this screen has been filed. */}
      <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 6 }}
           data-testid="cockpit-nothing-filed">
        {t('cockpit.notice.nothingFiled')}
      </div>
      {error && <ErrorNote error={error} />}

      {view && !view.available && (
        <div className="card" style={{ padding: 12, marginTop: 10, fontSize: 12.5 }}
             data-testid="cockpit-unavailable">
          <div>{t('cockpit.unavailable')}</div>
          {view.reason && (
            <div style={{ color: 'var(--muted)', marginTop: 4 }}>{view.reason}</div>
          )}
        </div>
      )}

      {view && view.available && (
        <div style={{ marginTop: 10 }}>
          {/* ---- 1. Everything Ellis has prepared ---------------------------- */}
          <div className="card" style={{ padding: 14 }} data-testid="cockpit-prepared">
            <div style={{ fontWeight: 600, fontSize: 13 }}>{t('cockpit.prepared.title')}</div>
            {view.filledCount != null && view.totalCount != null && (
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
                {t('cockpit.prepared.count', { filled: view.filledCount, total: view.totalCount })}
              </div>
            )}
            {view.prepared.length === 0 && view.documents.length === 0 && (
              <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 6 }}>
                {t('cockpit.prepared.empty')}
              </div>
            )}
            {view.prepared.length > 0 && (
              <ul style={{ margin: '6px 0 0 18px', fontSize: 12.5 }}>
                {view.prepared.map((f, i) => (
                  <li key={f.key || i}>
                    {f.label}{f.value ? `: ${f.value}` : ''}
                    <span style={{ color: 'var(--muted)' }}>
                      {' '}· {f.sourceKnown
                        ? t('cockpit.prepared.source', { source: f.source })
                        : t('cockpit.prepared.sourceUnknown')}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {/* The computed wage level — a suggestion, never a filed value. */}
            {view.wage && (
              <div style={{ fontSize: 12.5, marginTop: 8 }} data-testid="cockpit-wage">
                <span style={{ fontWeight: 600 }}>{t('cockpit.prepared.wage')}</span>
                {view.wage.computedLevel && (
                  <div>
                    {t('cockpit.prepared.wageLine', {
                      level: view.wage.computedLevel,
                      wage: money((view.wage.levels.find(
                        (l) => l.roman === view.wage.computedLevel) || {}).wage) || '—',
                      source: view.wage.source || '—',
                      asOf: view.wage.asOf || '—'
                    })}
                  </div>
                )}
                <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                  {t('h1b.wage.confirmNote')}
                </div>
              </div>
            )}
            {view.documents.length > 0 && (
              <div style={{ marginTop: 8 }} data-testid="cockpit-documents">
                <div style={{ fontSize: 12, fontWeight: 600 }}>{t('cockpit.prepared.documents')}</div>
                <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5, color: 'var(--muted)' }}>
                  {view.documents.map((d, i) => (
                    <li key={d.key || i}>
                      {d.label}{d.citation ? ` · ${d.citation}` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {view.narrative.available && (
              <div style={{ marginTop: 8 }} data-testid="cockpit-narrative">
                <div style={{ fontSize: 12, fontWeight: 600 }}>
                  {t('cockpit.prepared.narrative')}
                </div>
                <div style={{ fontSize: 11.5, color: '#c77700' }}>
                  {t('cockpit.prepared.narrativeDraft')}
                </div>
                {view.narrative.text && (
                  <details style={{ marginTop: 4 }}>
                    <summary style={{ fontSize: 12.5, cursor: 'pointer' }}>
                      {t('cockpit.prepared.narrative')}
                    </summary>
                    <div style={{ fontSize: 12.5, whiteSpace: 'pre-wrap', marginTop: 4 }}>
                      {view.narrative.text}
                    </div>
                  </details>
                )}
              </div>
            )}
            {view.notApplicable.length > 0 && (
              <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 8 }}>
                {t('cockpit.prepared.notApplicable', {
                  items: view.notApplicable.map((x) => x.label).join(', ')
                })}
              </div>
            )}
          </div>

          {/* ---- 2. What is still missing ------------------------------------ */}
          <div className="card" style={{ padding: 14, marginTop: 10 }}
               data-testid="cockpit-missing">
            <div style={{ fontWeight: 600, fontSize: 13 }}>
              {t('cockpit.missing.title')}
              {view.missingCount > 0 && (
                <span className="chip" style={{ marginLeft: 8 }}>
                  {t('cockpit.missing.count', { count: view.missingCount })}
                </span>
              )}
            </div>
            {view.missingCount === 0 ? (
              <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 4 }}>
                {t('cockpit.missing.none')}
              </div>
            ) : (
              <ul style={{ margin: '6px 0 0 18px', fontSize: 12.5 }}>
                {view.missing.map((m, i) => (
                  <li key={m.key || i}>
                    {m.label}
                    <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                      {t('cockpit.missing.input', { input: m.input })}
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {view.humanOnly.length > 0 && (
              <div style={{ marginTop: 8 }} data-testid="cockpit-human-only">
                <div style={{ fontSize: 12, fontWeight: 600, color: '#c77700' }}>
                  {t('cockpit.humanOnly.title')}
                </div>
                <ul style={{ margin: '4px 0 0 18px', fontSize: 12, color: '#c77700' }}>
                  {view.humanOnly.map((h, i) => <li key={i}>{h}</li>)}
                </ul>
              </div>
            )}
          </div>

          {/* ---- 3. The single action ---------------------------------------- */}
          <div className="card" style={{ padding: 14, marginTop: 10 }}
               data-testid="cockpit-action">
            <div style={{ display: 'flex', gap: 10, justifyContent: 'space-between',
                          alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ fontSize: 12.5 }} data-testid="cockpit-taps">
                <span style={{ fontWeight: 600 }}>{t('cockpit.taps.title')}: </span>
                {view.taps.known
                  ? (view.taps.exact != null
                      ? t('cockpit.taps.exact', { count: view.taps.exact })
                      : t('cockpit.taps.range', { min: view.taps.min, max: view.taps.max }))
                  : t('cockpit.taps.unknown')}
                <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{t('cockpit.taps.note')}</div>
              </div>
              {/* THE single action. It opens the applicant's own window on the
                  official page and stops: the close control lives inside the
                  window, so this button never becomes a second, ambiguous
                  "finish" the human could read as a submission. */}
              {!windowOpen && (
                <button className="btn" disabled={!view.action.enabled}
                        onClick={() => setWindowOpen(true)} data-testid="cockpit-open-window">
                  {t('cockpit.action.open')}
                </button>
              )}
            </div>
            {!view.action.enabled && (
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}
                   data-testid="cockpit-action-disabled">
                {t('cockpit.action.disabled', {
                  reason: view.action.reasonKey ? t(view.action.reasonKey)
                    : (view.action.reason || t('cockpit.action.notStarted'))
                })}
              </div>
            )}
            {/* The acts that stay the human's — named, always, beside the
                button and never behind it. */}
            <div style={{ marginTop: 10 }} data-testid="cockpit-human-acts">
              <div style={{ fontSize: 12, fontWeight: 600 }}>{t('cockpit.acts.title')}</div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
                {view.humanActs.map((a, i) => <HumanAct key={a.key || i} t={t} act={a} />)}
              </ul>
              <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 6 }}
                   data-testid="cockpit-never">
                {t('cockpit.acts.never')}
              </div>
            </div>
            {windowOpen && sessionCaseId && (
              <SecureWindow t={t} client={client} caseId={sessionCaseId}
                            onClose={() => setWindowOpen(false)} />
            )}
          </div>

          {/* Backend notices (preparation copies, DOL-use blocks, retention)
              render verbatim: they are the server's own honest wording. */}
          {view.notices.map((n, i) => (
            <div key={i} style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 8 }}>{n}</div>
          ))}
          {view.filedAt && (
            <div style={{ fontSize: 11.5, marginTop: 6 }}>
              <a href={view.filedAt.url} target="_blank" rel="noreferrer">
                {t('cockpit.filedAt', { host: view.filedAt.host })}
              </a>
            </div>
          )}
          {view.disclaimer && (
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8 }}>
              {view.disclaimer}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
