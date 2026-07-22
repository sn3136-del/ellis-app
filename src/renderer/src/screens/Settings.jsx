import { useState, useEffect } from 'react'
import { ellis, INTEGRATIONS } from '../lib/api.js'
import { useToast } from '../components/ui.jsx'
import { Icon } from '../components/icons.jsx'

export default function Settings({ onReplayTour, onReplayOnboarding }) {
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
    toast(st.available ? 'Kimi K3 connected — Ellis Immigration Intelligence is live' : st.reason === 'NO_KEY' ? 'Add a Moonshot API key first' : 'Could not reach Kimi K3 — check the key and endpoint')
  }

  async function toggleIntegration(id) {
    const integrations = { ...(s.integrations || {}), [id]: !s.integrations?.[id] }
    const next = { ...s, integrations }
    setS(next)
    await ellis.saveSettings(next)
    toast(integrations[id] ? `Connected to ${INTEGRATIONS.find((x) => x.id === id)?.label}` : `Disconnected ${INTEGRATIONS.find((x) => x.id === id)?.label}`)
  }

  return (
    <div>
      <div className="topbar">
        <h1>Settings</h1>
        <div className="topbar__actions"><button className="btn" onClick={save}>Save changes</button></div>
      </div>

      <div className="page" style={{ maxWidth: 760 }}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>Ask Ellis intelligence</div>
        <div className="card" style={{ padding: 22, marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Kimi K3 — Ellis Immigration Intelligence (primary)</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', marginBottom: 14 }}>
            Ellis runs on an immigration-tailored profile of Kimi K3 (Moonshot AI) — a frontier model with a 1M-token context and vision, specialized here for visas, document reading/translation, and the Trip.com one-click pipeline. <b>This build ships with a managed Kimi K3 key already active — no setup needed.</b> Enter your own key below only to bill usage to your organization instead (it takes precedence over the managed key).
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
              {kimiStatus.available ? `Connected to Kimi${kimiStatus.managed ? ' (managed key provisioned by your administrator — no setup needed)' : ''}. Available models: ${(kimiStatus.models || []).slice(0, 6).join(', ') || 'ready'}` : `Not connected${kimiStatus.reason === 'NO_KEY' ? ' — no API key set (personal or admin-provisioned).' : ' — ' + (kimiStatus.reason || 'unreachable') + '.'}`}
            </div>
          )}

          <div style={{ borderTop: '1px solid var(--line)', margin: '18px 0' }} />

          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Local AI — free, private, no API key</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', marginBottom: 14 }}>
            Turn Ask Ellis into a full LLM that runs entirely on your laptop — no key, no cost, nothing leaves your device. One-time setup: (1) download Ollama, (2) open Terminal and run <code style={{ background: 'var(--bg-2,#f3f3f3)', padding: '1px 6px', borderRadius: 4 }}>ollama pull {s.localAI?.model || 'llama3.1:8b'}</code>, (3) enable the toggle below. Ellis then answers any question and runs tasks through the local model, and falls back to the built-in engine if it isn’t running.
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            <button className="btn btn--sm" onClick={() => ellis.openExternal('https://ollama.com/download')}><Icon.download style={{ width: 15, height: 15 }} /> Download Ollama</button>
            <button className="btn btn--ghost btn--sm" onClick={checkLocalAI} disabled={checking}>{checking ? <><span className="spinner spinner--ink" /> Checking</> : 'Check connection'}</button>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, cursor: 'pointer', marginBottom: 12 }}>
            <input type="checkbox" checked={!!s.localAI?.enabled} onChange={(e) => setLocalAI({ enabled: e.target.checked })} />
            Use Local AI for Ask Ellis (when available)
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
            Paste an API key from console.anthropic.com to run the agent on Claude Fable 5 (Anthropic's most capable model, with automatic Opus 4.8 fallback). There is no free Claude API tier — for free, use Local AI above. Chain order: Kimi K3 → Claude → Local AI → built-in.
          </div>
          <div className="field"><label>Anthropic Claude API key</label>
            <input className="input" type="password" value={s.anthropicKey || ''} onChange={(e) => set({ anthropicKey: e.target.value })} placeholder="sk-ant-..." />
          </div>
          <div className="field"><label>Claude model</label>
            <select className="select" value={s.anthropicModel || 'claude-fable-5'} onChange={(e) => set({ anthropicModel: e.target.value })}>
              {['claude-fable-5', 'claude-opus-4-8', 'claude-sonnet-5', 'claude-haiku-4-5'].map((m) => <option key={m}>{m}</option>)}
            </select>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--muted-2)' }}>
            {s.kimi?.enabled && ((s.kimi?.apiKey || '').trim() || kimiStatus?.managed) ? 'Active: Kimi K3 immigration profile (falls back to Local AI / Claude / built-in on error).'
              : s.localAI?.enabled ? 'Active: Local AI (falls back to built-in if Ollama is off).'
              : (s.anthropicKey || '').trim() ? 'Active: Claude (falls back to built-in on error).'
              : 'Active: Ellis built-in engine (free, offline).'}
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
          </div>
        </div>

        <div className="eyebrow" style={{ marginBottom: 12 }}>Organization</div>
        <div className="card" style={{ padding: 22, marginBottom: 20 }}>
          <div className="field"><label>Organization name (appears on exported PDFs)</label>
            <input className="input" value={s.organizationName} onChange={(e) => set({ organizationName: e.target.value })} />
          </div>
          <div className="grid grid-2">
            <div className="field"><label>Default destination</label>
              <select className="select" value={s.defaultDestination} onChange={(e) => set({ defaultDestination: e.target.value })}>
                <option>USA</option><option>Canada</option>
              </select>
            </div>
            <div className="field"><label>Default translation language</label>
              <select className="select" value={s.defaultLanguage} onChange={(e) => set({ defaultLanguage: e.target.value })}>
                {['English', 'French', 'Simplified Chinese', 'Spanish'].map((l) => <option key={l}>{l}</option>)}
              </select>
            </div>
          </div>
        </div>

        <div className="eyebrow" style={{ marginBottom: 12 }}>Deadlines & compliance</div>
        <div className="card" style={{ padding: 22, marginBottom: 20 }}>
          <div className="field"><label>Remind me this many days before any expiry / deadline</label>
            <select className="select" value={s.reminderDays} onChange={(e) => set({ reminderDays: Number(e.target.value) })}>
              {[30, 45, 60, 90, 120].map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, cursor: 'pointer' }}>
            <input type="checkbox" checked={s.autoReview} onChange={(e) => set({ autoReview: e.target.checked })} />
            Auto-run a compliance scan when a case is opened
          </label>
        </div>

        <div className="eyebrow" style={{ marginBottom: 12 }}>Integrations</div>
        <div className="card" style={{ padding: 22, marginBottom: 20 }}>
          {INTEGRATIONS.map((it, i) => {
            const on = !!s.integrations?.[it.id]
            return (
              <div key={it.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 0', borderBottom: i === INTEGRATIONS.length - 1 ? 'none' : '1px solid var(--line)' }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  <div className="captile__icon" style={{ width: 34, height: 34 }}><Icon.plug style={{ width: 16, height: 16 }} /></div>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{it.label} {on && <span className="chip" style={{ marginLeft: 6 }}>Connected</span>}</div>
                    <div style={{ fontSize: 13, color: 'var(--muted)' }}>{it.desc}</div>
                  </div>
                </div>
                <button className={on ? 'btn btn--ghost btn--sm' : 'btn btn--sm'} onClick={() => toggleIntegration(it.id)}>{on ? 'Disconnect' : 'Connect'}</button>
              </div>
            )
          })}
        </div>

        <div className="eyebrow" style={{ marginBottom: 12 }}>Help & demo</div>
        <div className="card" style={{ padding: 22 }}>
          <Action title="Replay the product tour" desc="Walk through a template case step by step." btn="Start tour" onClick={onReplayTour} />
          <Action title="Replay the welcome animation" desc="See the intro shown on first launch." btn="Play" onClick={onReplayOnboarding} last />
        </div>
      </div>
    </div>
  )
}

function Action({ title, desc, btn, onClick, last }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 0', borderBottom: last ? 'none' : '1px solid var(--line)' }}>
      <div><div style={{ fontWeight: 600, fontSize: 14 }}>{title}</div><div style={{ fontSize: 13, color: 'var(--muted)' }}>{desc}</div></div>
      <button className="btn btn--ghost btn--sm" onClick={onClick}>{btn}</button>
    </div>
  )
}
