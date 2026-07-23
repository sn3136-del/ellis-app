import { useState, useEffect } from 'react'
import { ellis } from '../lib/api.js'
import { useToast } from '../components/ui.jsx'
import { Icon } from '../components/icons.jsx'

export default function Settings() {
  const toast = useToast()
  const [s, setS] = useState(null)
  const [llmStatus, setLlmStatus] = useState(null)
  const [checking, setChecking] = useState(false)
  const [kimiStatus, setKimiStatus] = useState(null)
  const [kimiChecking, setKimiChecking] = useState(false)

  useEffect(() => { ellis.getSettings().then(setS) }, [])
  if (!s) return null

  function set(patch) { setS({ ...s, ...patch }) }
  function setLocalAI(patch) { setS({ ...s, localAI: { ...(s.localAI || {}), ...patch } }) }
  function setKimi(patch) { setS({ ...s, kimi: { ...(s.kimi || {}), ...patch } }) }
  function setSmtp(patch) { setS({ ...s, smtp: { ...(s.smtp || {}), ...patch } }) }
  async function save() { await ellis.saveSettings(s); toast('Settings saved') }

  async function checkLocalAI() {
    setChecking(true)
    const st = await ellis.ai.localStatus({ endpoint: s.localAI?.endpoint })
    setChecking(false); setLlmStatus(st)
    if (st.available) toast(st.models?.length ? `Local AI connected · ${st.models.length} model(s)` : 'Ollama running — no models yet, pull one')
    else toast('Ollama not detected — install it and run it first')
  }

  async function checkKimi() {
    setKimiChecking(true)
    await ellis.saveSettings(s)
    const st = await ellis.ai.kimiStatus()
    setKimiChecking(false); setKimiStatus(st)
    toast(st.available ? 'Kimi K3 connected' : st.reason === 'NO_KEY' ? 'Add a Moonshot API key first' : 'Could not reach Kimi K3 — check the key and endpoint')
  }

  return (
    <div>
      <div className="topbar">
        <h1>Settings</h1>
        <div className="topbar__actions"><button className="btn" onClick={save}>Save changes</button></div>
      </div>

      <div className="page" style={{ maxWidth: 760 }}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>AI engine</div>
        <div className="card" style={{ padding: 22, marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Kimi K3 (primary)</div>
          <div style={{ fontSize: 12.5, color: 'var(--muted-2)', marginBottom: 8 }}>Used by the simulated demo pipeline only.</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', marginBottom: 14 }}>
            The demo pipeline's document reading, translation, and gap review can run on Kimi K3 (Moonshot AI). Enter a key below to enable it; without a key the deterministic built-in engine is used.
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            <button className="btn btn--sm" onClick={() => ellis.openExternal('https://platform.kimi.ai/console/api-keys')}><Icon.globe style={{ width: 15, height: 15 }} /> Get a Kimi API key</button>
            <button className="btn btn--ghost btn--sm" onClick={checkKimi} disabled={kimiChecking}>{kimiChecking ? <><span className="spinner spinner--ink" /> Checking</> : 'Check connection'}</button>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, cursor: 'pointer', marginBottom: 12 }}>
            <input type="checkbox" checked={!!s.kimi?.enabled} onChange={(e) => setKimi({ enabled: e.target.checked })} />
            Use Kimi K3 as the primary engine (when a key is set)
          </label>
          <div className="grid grid-2">
            <div className="field"><label>Moonshot API key</label>
              <input className="input" type="password" value={s.kimi?.apiKey || ''} onChange={(e) => setKimi({ apiKey: e.target.value })} placeholder="sk-..." />
            </div>
            <div className="field"><label>Model</label>
              <select className="select" value={s.kimi?.model || 'kimi-k3'} onChange={(e) => setKimi({ model: e.target.value })}>
                {['kimi-k3', 'kimi-k2.7-code', 'kimi-k2.6'].map((m) => <option key={m}>{m}</option>)}
              </select>
            </div>
          </div>
          {kimiStatus && (
            <div style={{ fontSize: 12.5, color: 'var(--muted-2)', marginBottom: 4 }}>
              {kimiStatus.available ? `Connected to Kimi. Available models: ${(kimiStatus.models || []).slice(0, 6).join(', ') || 'ready'}` : `Not connected${kimiStatus.reason === 'NO_KEY' ? ' — no API key set.' : ' — ' + (kimiStatus.reason || 'unreachable') + '.'}`}
            </div>
          )}

          <div style={{ borderTop: '1px solid var(--line)', margin: '18px 0' }} />

          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Local AI — free, private, no API key</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', marginBottom: 14 }}>
            Run the demo pipeline's language tasks entirely on this laptop — no key, no cost, nothing leaves the device. One-time setup: (1) download Ollama, (2) open Terminal and run <code style={{ background: 'var(--bg-2,#f3f3f3)', padding: '1px 6px', borderRadius: 4 }}>ollama pull {s.localAI?.model || 'llama3.1:8b'}</code>, (3) enable the toggle below.
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            <button className="btn btn--sm" onClick={() => ellis.openExternal('https://ollama.com/download')}><Icon.download style={{ width: 15, height: 15 }} /> Download Ollama</button>
            <button className="btn btn--ghost btn--sm" onClick={checkLocalAI} disabled={checking}>{checking ? <><span className="spinner spinner--ink" /> Checking</> : 'Check connection'}</button>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, cursor: 'pointer', marginBottom: 12 }}>
            <input type="checkbox" checked={!!s.localAI?.enabled} onChange={(e) => setLocalAI({ enabled: e.target.checked })} />
            Use Local AI (when available)
          </label>
          <div className="grid grid-2">
            <div className="field"><label>Local model name</label>
              <input className="input" value={s.localAI?.model || ''} onChange={(e) => setLocalAI({ model: e.target.value })} placeholder="llama3.1:8b" />
            </div>
            <div className="field"><label>Ollama endpoint</label>
              <input className="input" value={s.localAI?.endpoint || ''} onChange={(e) => setLocalAI({ endpoint: e.target.value })} placeholder="http://127.0.0.1:11434" />
            </div>
          </div>
          {llmStatus && (
            <div style={{ fontSize: 12.5, color: 'var(--muted-2)' }}>
              {llmStatus.available
                ? `Connected. Installed models: ${llmStatus.models?.length ? llmStatus.models.join(', ') : 'none — run "ollama pull ' + (s.localAI?.model || 'llama3.2') + '"'}`
                : 'Not detected. Make sure Ollama is installed and running.'}
            </div>
          )}

          <div style={{ borderTop: '1px solid var(--line)', margin: '18px 0' }} />

          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Anthropic Claude — Fable 5 (optional, paid key)</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', marginBottom: 14 }}>
            Paste an API key from console.anthropic.com to run the demo pipeline on Claude Fable 5. There is no free Claude API tier — for free, use Local AI above. Chain order: Kimi K3 → Claude → Local AI → built-in.
          </div>
          <div className="field"><label>Anthropic Claude API key</label>
            <input className="input" type="password" value={s.anthropicKey || ''} onChange={(e) => set({ anthropicKey: e.target.value })} placeholder="sk-ant-..." />
          </div>
          <div className="field"><label>Claude model</label>
            <select className="select" value={s.anthropicModel || 'claude-fable-5'} onChange={(e) => set({ anthropicModel: e.target.value })}>
              {['claude-fable-5', 'claude-opus-4-8', 'claude-sonnet-5', 'claude-haiku-4-5'].map((m) => <option key={m}>{m}</option>)}
            </select>
          </div>
        </div>

        <div className="eyebrow" style={{ marginBottom: 12 }}>Email delivery</div>
        <div className="card" style={{ padding: 22, marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Send traveler emails from your own inbox</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', marginBottom: 14 }}>
            Every visa update goes to the traveler's own email (entered on their application). Choose the address those emails are sent <b>from</b>: enter your organization's email and an SMTP app password to send from your inbox. Leave both blank to send through this computer's Mail app.
          </div>
          <div className="grid grid-2">
            <div className="field"><label>Your sender email</label>
              <input className="input" value={s.smtp?.user || ''} onChange={(e) => setSmtp({ user: e.target.value })} placeholder="e.g. visas@trip.com" autoComplete="off" />
            </div>
            <div className="field"><label>SMTP app password</label>
              <input className="input" type="password" value={s.smtp?.appPassword || ''} onChange={(e) => setSmtp({ appPassword: e.target.value })} placeholder="Leave blank to use the Mail app" autoComplete="off" />
            </div>
          </div>
          <div className="grid grid-2">
            <div className="field"><label>SMTP host</label>
              <input className="input" value={s.smtp?.host || ''} onChange={(e) => setSmtp({ host: e.target.value })} placeholder="smtp.gmail.com" />
            </div>
            <div className="field"><label>SMTP port</label>
              <input className="input" value={s.smtp?.port || ''} onChange={(e) => setSmtp({ port: Number(e.target.value) || 587 })} placeholder="587" />
            </div>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--muted-2)', marginBottom: 14 }}>
            {(s.smtp?.user || '').trim() && (s.smtp?.appPassword || '').trim()
              ? `Traveler emails will be sent from ${s.smtp.user} via SMTP.`
              : 'Traveler emails will be sent through this computer’s Mail app. Add your email + app password above to send from your own inbox.'}
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Trip.com filing intake address (agency / visa centre)</label>
            <input className="input" value={s.tripFiling?.endpoint || ''}
              onChange={(e) => setS({ ...s, tripFiling: { ...(s.tripFiling || {}), endpoint: e.target.value } })}
              placeholder="Agency / visa-centre intake address — required for automatic transmission" />
            <div style={{ fontSize: 12.5, color: 'var(--muted-2)', marginTop: 6 }}>Used by the simulated demo pipeline only.</div>
          </div>
        </div>
      </div>
    </div>
  )
}
