// The DS-160 inside Ellis.
//
// The government asks 85 questions across 15 screens. This surface shows the
// split that IS the product: Ellis fills the factual ones from the passport
// read and the traveller's answers, and the traveller keeps the ones only
// they may answer — the sworn history, the five Security and Background
// parts, the photograph, and the signature.
//
// Every question here is the government's own wording, and every dropdown
// carries CEAC's own choices with the value codes it actually posts, read
// from the live form in an attended session (2026-08-18). Nothing on this
// screen is invented: an option the portal does not offer cannot appear.
import { useEffect, useState } from 'react'
import { Loading, ErrorNote } from '../ui.jsx'

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #64748b)'
const BLUE = 'var(--trip-blue, #287dfa)'

// CEAC's own page names -> what a traveller would call them.
const PAGE_TITLES = {
  getting_started: 'Getting started',
  confirm_application_id: 'Your application ID',
  personal1: 'About you',
  personal2: 'Nationality and ID numbers',
  travel: 'Your trip',
  travel_companions: 'Travelling companions',
  previous_us_travel: 'Previous US travel',
  address_phone: 'Address and contact',
  passport: 'Passport',
  us_contact: 'Where you will stay',
  family: 'Family',
  work_education_present: 'Work and education',
  work_education_previous: 'Previous work and study',
  work_education_additional: 'Background details',
  security_and_background_1: 'Security and background'
}

function Stat({ n, label, tone }) {
  return (
    <div style={{ flex: '1 1 120px', padding: '14px 16px', borderRadius: 14,
                  background: tone === 'ellis' ? '#eef5ff' : '#fff7ed',
                  border: `1px solid ${tone === 'ellis' ? '#dbe7fb' : '#fed7aa'}` }}>
      <div style={{ fontSize: 26, fontWeight: 800,
                    color: tone === 'ellis' ? BLUE : '#c2740a' }}>{n}</div>
      <div style={{ fontSize: 12, color: GRAY, marginTop: 2 }}>{label}</div>
    </div>
  )
}

// One question, rendered as the control the portal uses. A select shows the
// portal's real options; free text shows its real maxlength.
function Question({ q }) {
  const opts = q.options || q.options_sample
  return (
    <div style={{ padding: '11px 14px', borderRadius: 12, marginTop: 8,
                  background: q.applicant_only ? '#fffbf5' : '#f8fafc',
                  border: `1px solid ${q.applicant_only ? '#fed7aa' : '#e2e8f0'}` }}>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'space-between',
                    alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13, color: NAVY, lineHeight: 1.5, flex: '1 1 300px' }}>
          {q.question}
        </div>
        <span style={{ fontSize: 10.5, fontWeight: 800, whiteSpace: 'nowrap',
                       padding: '3px 9px', borderRadius: 999,
                       background: q.applicant_only ? '#fed7aa' : '#dbe7fb',
                       color: q.applicant_only ? '#8a4b04' : '#1b5fd0' }}>
          {q.applicant_only ? 'YOU ANSWER' : 'ELLIS FILLS'}
        </span>
      </div>

      {/* The portal's own answer vocabulary — never a paraphrase. */}
      {q.kind === 'select' && opts && (
        <select className="input" disabled
                style={{ marginTop: 7, width: '100%', fontSize: 12.5 }}>
          {opts.map((o, i) => (
            <option key={i}>{typeof o === 'string' ? o : o.label}</option>
          ))}
        </select>
      )}
      {q.kind === 'select' && !opts && q.options_source === 'country_list' && (
        <div style={{ fontSize: 11.5, color: GRAY, marginTop: 6 }}>
          the official country list (~240 entries)
        </div>
      )}
      {q.kind === 'radio' && (
        <div style={{ display: 'flex', gap: 14, marginTop: 7 }}>
          {(q.options || ['Yes', 'No']).map((o) => (
            <label key={o} style={{ fontSize: 12.5, color: NAVY }}>
              <input type="radio" disabled style={{ marginRight: 5 }} />{o}
            </label>
          ))}
        </div>
      )}
      {(q.kind === 'text' || q.kind === 'textarea') && (
        <input className="input" disabled placeholder=""
               style={{ marginTop: 7, width: '100%', fontSize: 12.5 }} />
      )}
      {q.kind === 'split_date' && (
        <div style={{ fontSize: 11.5, color: GRAY, marginTop: 6 }}>
          date — the official form splits this into day, month and year
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 6 }}>
        {q.conditional && (
          <span style={{ fontSize: 11, color: '#8a4b04' }}>↳ {q.conditional}</span>
        )}
        {q.maxlength && (
          <span style={{ fontSize: 11, color: GRAY }}>up to {q.maxlength} characters</span>
        )}
        {q.na_checkbox && (
          <span style={{ fontSize: 11, color: GRAY }}>has a “does not apply” option</span>
        )}
      </div>
    </div>
  )
}

export default function Ds160Panel({ client }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState('')

  useEffect(() => {
    let live = true
    client.ds160Questions()
      .then((d) => { if (live) setData(d) })
      .catch((e) => { if (live) setError({ message: e.message }) })
    return () => { live = false }
  }, [])

  if (error) return <ErrorNote error={error} />
  if (!data) return <Loading label="Loading the DS-160" />

  const c = data.counts
  return (
    <div data-testid="ds160-panel" style={{ marginTop: 8 }}>
      <div style={{ fontSize: 20, fontWeight: 800, color: NAVY, letterSpacing: -0.3 }}>
        The US visa form, filled for you
      </div>
      <div style={{ fontSize: 13, color: GRAY, marginTop: 4, lineHeight: 1.55 }}>
        The DS-160 asks {c.total} questions across {data.pages.length} screens on{' '}
        {data.portal}. Answer them here, once.
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 16, flexWrap: 'wrap' }}>
        <Stat n={c.ellis_fills} label="Ellis fills these" tone="ellis" />
        <Stat n={c.applicant_answers} label="only you can answer" tone="you" />
      </div>

      {/* The rule, in the government's words. Never buried. */}
      <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 12,
                    background: '#f8fafc', border: '1px solid #e2e8f0',
                    fontSize: 12, color: NAVY, lineHeight: 1.6 }}
           data-testid="ds160-signature-rule">
        {data.signature_rule}
      </div>
      <div style={{ marginTop: 8, fontSize: 11.5, color: GRAY, lineHeight: 1.55 }}>
        {data.preparer_block}
      </div>

      {data.pages.map((p) => {
        const mine = p.questions.filter((q) => !q.applicant_only).length
        const yours = p.questions.length - mine
        const isOpen = open === p.page
        return (
          <div key={p.page} className="card" style={{ padding: 0, marginTop: 10,
                                                      borderRadius: 14, overflow: 'hidden' }}>
            <button onClick={() => setOpen(isOpen ? '' : p.page)}
                    data-testid={`ds160-page-${p.page}`}
                    style={{ display: 'flex', width: '100%', gap: 12, cursor: 'pointer',
                             justifyContent: 'space-between', alignItems: 'center',
                             padding: '13px 16px', border: 'none', background: '#fff',
                             textAlign: 'left' }}>
              <span style={{ fontWeight: 700, fontSize: 13.5, color: NAVY }}>
                {PAGE_TITLES[p.page] || p.page}
              </span>
              <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                {mine > 0 && (
                  <span style={{ fontSize: 10.5, fontWeight: 800, padding: '2px 8px',
                                 borderRadius: 999, background: '#dbe7fb', color: '#1b5fd0' }}>
                    {mine} auto
                  </span>
                )}
                {yours > 0 && (
                  <span style={{ fontSize: 10.5, fontWeight: 800, padding: '2px 8px',
                                 borderRadius: 999, background: '#fed7aa', color: '#8a4b04' }}>
                    {yours} you
                  </span>
                )}
                <span style={{ color: GRAY, fontSize: 15 }}>{isOpen ? '−' : '+'}</span>
              </span>
            </button>
            {isOpen && (
              <div style={{ padding: '0 16px 14px' }}>
                {p.applicant_only_page && (
                  <div style={{ fontSize: 11.5, color: '#8a4b04', marginBottom: 4 }}>
                    Sworn under penalty of perjury — these are yours alone to answer.
                  </div>
                )}
                {p.questions.map((q, i) => <Question key={q.field || i} q={q} />)}
                {p.note && (
                  <div style={{ fontSize: 11, color: GRAY, marginTop: 8, lineHeight: 1.5 }}>
                    {p.note}
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}

      <div style={{ fontSize: 11, color: GRAY, marginTop: 12, lineHeight: 1.5 }}>
        Questions and answer choices read from the official form, {data.source}.
      </div>
    </div>
  )
}
