// Ask Ellis — the floating H-1B case assistant. A fixed button bottom-right
// (with a soft pulsing halo) opens an airy chat panel wired to POST
// /h1b/cases/{id}/assistant. Honesty rules: the attorney disclaimer is pinned
// at the top of the panel as one compact line with the full text one tap away
// (never scrolled away, never deleted), and every action the assistant
// reports renders through assistantActionMeta — a denied action shows AS
// denied, and an unconfirmed one can never display as performed. Locale rides
// with every message so the reply speaks the UI language.
import { useEffect, useRef, useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { createVisaClient } from '../../lib/visaBackend.js'
import {
  newSession, newAdminSession, newEmployerSession, assistantActionMeta
} from '../../lib/visaSession.js'
import { PipelineIllustration, ShieldIllustration } from './Illustrations.jsx'

function sessionForPersona(persona) {
  if (persona === 'admin') return newAdminSession()
  if (persona === 'employer') return newEmployerSession()
  return newSession()
}

// One reported action, rendered honestly. Denied = red outline + the denied
// label; done = filled chip; anything else = "not performed" (fail-safe, amber
// outline) — an unperformed act must never wear the success chip.
function ActionChip({ t, action }) {
  const meta = assistantActionMeta(action)
  const style = meta.denied
    ? { border: '1px solid #d33', color: '#d33', background: 'transparent' }
    : meta.done ? {}
    : { border: '1px solid #c77700', color: '#c77700', background: 'transparent' }
  return (
    <span className={'chip' + (meta.done ? ' chip--ink' : '')} style={style}
          title={meta.detail || undefined}
          data-testid={meta.denied ? 'askellis-action-denied' : 'askellis-action'}>
      {meta.label}{meta.label ? ' · ' : ''}{t(meta.i18nKey)}
    </span>
  )
}

export default function AskEllis({ caseId, persona = 'applicant' }) {
  const { t, lang } = useLocale()
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // messages: {role: 'user'|'ellis', text, actions?, suggestions?}
  const [messages, setMessages] = useState([])
  const [client, setClient] = useState(() => createVisaClient(sessionForPersona(persona)))
  const listRef = useRef(null)

  // Persona switches (rare) rebuild the client; a case switch clears the
  // thread so one case's conversation never bleeds into another's.
  useEffect(() => { setClient(createVisaClient(sessionForPersona(persona))) }, [persona])
  useEffect(() => { setMessages([]); setError('') }, [caseId])

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages, busy, open])

  if (!caseId) return null

  async function send(text) {
    const message = String(text != null ? text : input).trim()
    if (!message || busy) return
    setInput(''); setError(''); setBusy(true)
    // Backend contract (assistant_api.HistoryTurn): {role, text}, last 20 max.
    const history = messages.slice(-12).map((m) => ({
      role: m.role === 'ellis' ? 'assistant' : 'user', text: m.text
    }))
    setMessages((p) => [...p, { role: 'user', text: message }])
    try {
      const res = await client.h1bAssistant(caseId, { message, locale: lang, history })
      setMessages((p) => [...p, {
        role: 'ellis',
        text: (res && (res.reply || res.message)) || '',
        actions: Array.isArray(res && res.actions) ? res.actions : [],
        suggestions: Array.isArray(res && res.suggestions) ? res.suggestions : []
      }])
    } catch (e) {
      setError(e.message || t('askellis.error'))
    }
    setBusy(false)
  }

  const lastEllis = [...messages].reverse().find((m) => m.role === 'ellis')

  return (
    <>
      {/* Floating opener — above content, all personas, with a gentle halo. */}
      <div style={{ position: 'fixed', right: 22, bottom: 22, zIndex: 60 }}>
        <span className="illo-pulse" aria-hidden="true"
          style={{ position: 'absolute', inset: -6, borderRadius: 999,
                   background: 'rgba(38,129,255,0.35)', filter: 'blur(10px)',
                   pointerEvents: 'none' }} />
        <button type="button" className="btn" data-testid="askellis-button"
          style={{ position: 'relative', borderRadius: 999,
                   boxShadow: '0 6px 20px rgba(38,129,255,0.35)' }}
          onClick={() => setOpen((v) => !v)}>
          {open ? t('askellis.close') : t('askellis.button')}
        </button>
      </div>

      {open && (
        <div className="card anim-rise" data-testid="askellis-panel"
          style={{ position: 'fixed', right: 22, bottom: 80, zIndex: 60,
                   width: 'min(400px, calc(100vw - 44px))', maxHeight: '72vh',
                   borderRadius: 20,
                   display: 'flex', flexDirection: 'column', overflow: 'hidden',
                   boxShadow: '0 14px 44px rgba(15,41,77,0.22)' }}>
          {/* Header + the attorney disclaimer, PINNED at the panel top:
              one compact line, full text one tap away. */}
          <div style={{ padding: '16px 18px 12px',
                        borderBottom: '1px solid var(--line, #e5e7eb)' }}>
            <div style={{ fontWeight: 800, fontSize: 15,
                          color: 'var(--trip-navy, #0f294d)' }}>{t('askellis.title')}</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
              {t('askellis.sub')}
            </div>
            <details data-testid="askellis-disclaimer" style={{ marginTop: 8 }}>
              <summary style={{ listStyle: 'none', display: 'flex', alignItems: 'center',
                                gap: 8, cursor: 'pointer', fontSize: 11,
                                color: 'var(--muted)' }}>
                <ShieldIllustration size={22} />
                <span style={{ flex: 1, minWidth: 0 }}>{t('h1b.disclaimer.short')}</span>
              </summary>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 6,
                            lineHeight: 1.55 }}>
                {t('h1b.disclaimer')}
              </div>
            </details>
          </div>

          <div ref={listRef} style={{ flex: 1, overflowY: 'auto', padding: 16,
                                      display: 'flex', flexDirection: 'column', gap: 12 }}>
            {messages.length === 0 && !busy && (
              <div style={{ textAlign: 'center', padding: '14px 6px 6px' }}>
                <span style={{ display: 'grid', placeItems: 'center' }}>
                  <PipelineIllustration size={130} />
                </span>
                <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 8,
                              lineHeight: 1.5 }}>
                  {t('askellis.empty')}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className="anim-rise"
                   style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                            maxWidth: '92%' }}>
                <div style={{
                  padding: '9px 13px', fontSize: 13.5, lineHeight: 1.5,
                  whiteSpace: 'pre-wrap',
                  borderRadius: 14,
                  borderBottomRightRadius: m.role === 'user' ? 5 : 14,
                  borderBottomLeftRadius: m.role === 'user' ? 14 : 5,
                  background: m.role === 'user'
                    ? 'var(--trip-blue, #2681ff)' : 'var(--bg-2, #f3f6fb)',
                  color: m.role === 'user' ? '#fff' : 'inherit'
                }}>
                  {m.text}
                </div>
                {m.role === 'ellis' && Array.isArray(m.actions) && m.actions.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                      {t('askellis.actionsTaken')}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {m.actions.map((a, j) => <ActionChip key={j} t={t} action={a} />)}
                    </div>
                  </div>
                )}
              </div>
            ))}
            {busy && (
              <div className="illo-pulse" style={{ fontSize: 13, color: 'var(--muted)' }}>
                {t('askellis.thinking')}
              </div>
            )}
            {error && (
              <div style={{ fontSize: 12.5, color: '#d33' }} data-testid="askellis-error">
                {error}
              </div>
            )}
          </div>

          {/* Suggestion chips prefill the input — the user always presses Send. */}
          {lastEllis && Array.isArray(lastEllis.suggestions) && lastEllis.suggestions.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '0 14px 10px' }}>
              {lastEllis.suggestions.map((s, i) => (
                <button key={i} type="button" className="chip"
                        style={{ cursor: 'pointer' }}
                        onClick={() => setInput(String(s))}>
                  {String(s)}
                </button>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, padding: 14,
                        borderTop: '1px solid var(--line, #e5e7eb)' }}>
            <input className="input" style={{ flex: 1, borderRadius: 999 }} value={input}
                   placeholder={t('askellis.placeholder')}
                   onChange={(e) => setInput(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') send() }} />
            <button className="btn" disabled={busy || !input.trim()} onClick={() => send()}>
              {t('askellis.send')}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
