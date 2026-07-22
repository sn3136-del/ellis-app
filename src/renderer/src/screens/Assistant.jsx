import { useState, useRef, useEffect } from 'react'
import { ellis, ROLES } from '../lib/api.js'
import { Icon } from '../components/icons.jsx'
import { ErrorNote } from '../components/ui.jsx'

export default function Assistant({ role, onSettings }) {
  const [history, setHistory] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const scroller = useRef(null)

  useEffect(() => { scroller.current?.scrollTo(0, scroller.current.scrollHeight) }, [history, loading])

  async function send(text) {
    const msg = (text || input).trim()
    if (!msg) return
    setInput(''); setError(null)
    const next = [...history, { role: 'user', content: msg }]
    setHistory(next); setLoading(true)
    const res = await ellis.ai.assistantChat({ role, message: msg, history })
    setLoading(false)
    if (!res.ok) return setError(res.error)
    setHistory([...next, { role: 'assistant', content: res.data.reply }])
  }

  const STARTERS = {
    immigrant: ['What is the difference between OPT and a work visa?', 'How long can I stay on a visitor visa?', 'What documents do I need for a study permit?'],
    employer: ['What are my H-1B compliance obligations?', 'How does LMIA work in Canada?', 'What is a public access file?'],
    counsel: ['Summarize the H-1B cap process', 'What triggers an RFE on an O-1?', 'Compare PERM vs Express Entry timelines']
  }

  return (
    <div>
      <div className="topbar"><h1>Ask Ellis</h1></div>
      <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 64px)' }}>
        <div ref={scroller} style={{ flex: 1, overflowY: 'auto', padding: 32 }}>
          <div style={{ maxWidth: 760, margin: '0 auto' }}>
            {history.length === 0 && (
              <div style={{ textAlign: 'center', paddingTop: 30 }}>
                <div className="captile__icon" style={{ width: 54, height: 54, margin: '0 auto 16px' }}><Icon.spark style={{ width: 26, height: 26 }} /></div>
                <h2 className="h-serif" style={{ fontSize: 30, marginBottom: 8 }}>How can Ellis help?</h2>
                <p style={{ color: 'var(--muted)', marginBottom: 22 }}>General immigration guidance for the {ROLES[role].label.toLowerCase()}. For case-specific answers, open a case and use Ask Ellis there.</p>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
                  {STARTERS[role].map((s) => <button key={s} className="chip" style={{ cursor: 'pointer' }} onClick={() => send(s)}>{s}</button>)}
                </div>
              </div>
            )}
            {history.map((m, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 16 }}>
                <div className={m.role === 'user' ? 'card card--ink' : 'card'} style={{ padding: '12px 16px', maxWidth: '78%' }}>
                  <p className="prose" style={{ color: m.role === 'user' ? '#fff' : 'var(--ink-2)' }}>{m.content}</p>
                </div>
              </div>
            ))}
            {loading && <div style={{ display: 'flex', gap: 10, alignItems: 'center', color: 'var(--muted)' }}><span className="spinner spinner--ink" /> Ellis is thinking...</div>}
            <ErrorNote error={error} onSettings={onSettings} />
          </div>
        </div>
        <div style={{ borderTop: '1px solid var(--line)', padding: 20 }}>
          <div style={{ maxWidth: 760, margin: '0 auto', display: 'flex', gap: 10 }}>
            <input className="input" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} placeholder="Ask Ellis anything about immigration..." />
            <button className="btn" onClick={() => send()} disabled={loading || !input.trim()}>Send</button>
          </div>
        </div>
      </div>
    </div>
  )
}
