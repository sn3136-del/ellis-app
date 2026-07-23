// Trip.com first-run administrator setup (Phase 7). Collects tenant + provider
// configuration. Secrets are sent to the backend ONCE and never returned — after
// saving, only a redacted fingerprint is shown. No provider credential is ever
// stored in Electron; everything lives in the backend vault. Connection tests +
// a real "Send test email" verify the configuration.
import { useEffect, useState } from 'react'
import { useToast, Loading, ErrorNote } from '../components/ui.jsx'
import { createVisaClient } from '../lib/visaBackend.js'
import { newAdminSession } from '../lib/visaSession.js'

const SECRET_LABELS = {
  kimi_api_key: 'Kimi K3 API key',
  browserbase_api_key: 'Browserbase API key',
  google_service_account_json: 'Google service account JSON (optional; ADC preferred)',
  email_credential: 'Email SMTP/API credential',
  trip_client_secret: 'Trip.com client secret',
  trip_webhook_secret: 'Trip.com webhook secret'
}

function Fingerprint({ info }) {
  if (!info?.configured) return <span className="chip">not configured</span>
  return <span className="chip chip--ink" title="redacted fingerprint">configured · {info.fingerprint}</span>
}

function Field({ label, value, onChange, type = 'text', placeholder }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input className="input" type={type} value={value || ''} placeholder={placeholder}
             onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

export default function SetupWizard() {
  const toast = useToast()
  const [client] = useState(() => createVisaClient(newAdminSession()))
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [testEmailTo, setTestEmailTo] = useState('')

  // Non-secret config (prefilled from status). Secrets are NEVER prefilled.
  const [form, setForm] = useState({
    tenant_name: '', admin_email: '', data_region: 'us', retention_days: 365,
    email: { provider: 'smtp', sender: '', reply_to: '', host: '', port: 587, username: '', api_endpoint: '' },
    google: { project: '', location: 'us', ocr_processor_id: '', form_processor_id: '' },
    trip: { base_url: '', sandbox_base_url: '', client_id: '', signing_method: 'hmac-sha256' }
  })
  const [secrets, setSecrets] = useState({})  // component -> new value (write-only)

  async function load() {
    try {
      const s = await client.setupStatus()
      setStatus(s)
      setForm((f) => ({
        ...f,
        tenant_name: s.tenant_name || f.tenant_name,
        admin_email: s.admin_email || f.admin_email,
        data_region: s.config?.data_region || f.data_region,
        retention_days: s.config?.retention_days || f.retention_days,
        email: { ...f.email, ...(s.config?.email || {}) },
        google: { ...f.google, ...(s.config?.google || {}) },
        trip: { ...f.trip, ...(s.config?.trip || {}) }
      }))
    } catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => { load() }, [])

  function up(path, value) {
    setForm((f) => {
      const next = { ...f }
      const parts = path.split('.')
      if (parts.length === 1) next[parts[0]] = value
      else next[parts[0]] = { ...next[parts[0]], [parts[1]]: value }
      return next
    })
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      // Only send secrets the admin actually typed (write-only), plus config.
      const payload = { ...form, ...secrets }
      const res = await client.saveSetup(payload)
      setStatus(res)
      setSecrets({})  // clear typed secrets from memory after submit
      toast(res.setup_complete ? 'Setup complete' : 'Saved — more required')
    } catch (e) { setError({ message: e.message }) }
    setBusy(false)
  }

  async function test(component) {
    try {
      const r = await client.testSetupComponent(component)
      toast(`${SECRET_LABELS[component] || component}: ${r.status || (r.ok ? 'ok' : 'failed')}`)
    } catch (e) { toast(e.message) }
  }

  async function sendTest() {
    if (!testEmailTo) { toast('Enter a destination email'); return }
    try {
      const r = await client.sendTestEmail(testEmailTo)
      toast(r.delivered ? 'Test email delivered' : `Not delivered: ${r.status || r.note || 'no provider'}`)
    } catch (e) { toast(e.message) }
  }

  async function revoke(component) {
    try { await client.revokeSetupComponent(component); await load(); toast('Credential revoked') }
    catch (e) { toast(e.message) }
  }

  if (!status) return <Loading label="Loading setup" />
  const cfg = status.configured || {}

  return (
    <div className="screen" style={{ maxWidth: 860 }}>
      <div className="screen__head">
        <div>
          <div className="eyebrow">Trip.com IT</div>
          <h1>First-run setup</h1>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>
            Provider credentials are stored only in Ellis's backend vault — never in this app,
            and never shown again after you save. {status.setup_complete
              ? <strong style={{ color: 'var(--ok, #16a34a)' }}> Setup is complete.</strong>
              : <strong> Still required: {status.missing.join(', ') || '—'}.</strong>}
          </div>
        </div>
      </div>
      {error && <ErrorNote error={error} />}

      <section className="card" style={{ padding: 18, marginBottom: 14 }}>
        <div className="eyebrow">Tenant</div>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <Field label="Trip.com tenant name" value={form.tenant_name} onChange={(v) => up('tenant_name', v)} />
          <Field label="Administrator email" value={form.admin_email} onChange={(v) => up('admin_email', v)} />
          <Field label="Data region" value={form.data_region} onChange={(v) => up('data_region', v)} placeholder="us | eu | ap" />
          <Field label="Retention (days)" type="number" value={form.retention_days} onChange={(v) => up('retention_days', Number(v))} />
        </div>
      </section>

      <section className="card" style={{ padding: 18, marginBottom: 14 }}>
        <div className="eyebrow">AI + document providers</div>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <div className="field">
            <label>{SECRET_LABELS.kimi_api_key} <Fingerprint info={cfg.kimi_api_key} /></label>
            <input className="input" type="password" autoComplete="off" placeholder="sk-…"
                   value={secrets.kimi_api_key || ''} onChange={(e) => setSecrets((s) => ({ ...s, kimi_api_key: e.target.value }))} />
          </div>
          <div className="field">
            <label>{SECRET_LABELS.browserbase_api_key} <Fingerprint info={cfg.browserbase_api_key} /></label>
            <input className="input" type="password" autoComplete="off"
                   value={secrets.browserbase_api_key || ''} onChange={(e) => setSecrets((s) => ({ ...s, browserbase_api_key: e.target.value }))} />
          </div>
          <Field label="Google Cloud project" value={form.google.project} onChange={(v) => up('google.project', v)} />
          <Field label="Document AI OCR processor ID" value={form.google.ocr_processor_id} onChange={(v) => up('google.ocr_processor_id', v)} />
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => test('kimi_api_key')}>Test Kimi</button>
          <button className="btn btn--ghost btn--sm" onClick={() => test('browserbase_api_key')}>Test Browserbase</button>
          {cfg.kimi_api_key?.configured && <button className="btn btn--ghost btn--sm" onClick={() => revoke('kimi_api_key')}>Revoke Kimi</button>}
        </div>
      </section>

      <section className="card" style={{ padding: 18, marginBottom: 14 }}>
        <div className="eyebrow">Transactional email</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
          A sender address alone is not enough — SMTP or API credentials are required to send.
        </div>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <div className="field">
            <label>Provider</label>
            <select className="input" value={form.email.provider} onChange={(e) => up('email.provider', e.target.value)}>
              <option value="smtp">SMTP</option>
              <option value="api">API provider</option>
            </select>
          </div>
          <Field label="Sender address" value={form.email.sender} onChange={(v) => up('email.sender', v)} />
          <Field label="Reply-to" value={form.email.reply_to} onChange={(v) => up('email.reply_to', v)} />
          {form.email.provider === 'smtp' ? <>
            <Field label="SMTP host" value={form.email.host} onChange={(v) => up('email.host', v)} />
            <Field label="SMTP port" type="number" value={form.email.port} onChange={(v) => up('email.port', Number(v))} />
            <Field label="SMTP username" value={form.email.username} onChange={(v) => up('email.username', v)} />
          </> : <Field label="API endpoint" value={form.email.api_endpoint} onChange={(v) => up('email.api_endpoint', v)} />}
          <div className="field">
            <label>{SECRET_LABELS.email_credential} <Fingerprint info={cfg.email_credential} /></label>
            <input className="input" type="password" autoComplete="off"
                   value={secrets.email_credential || ''} onChange={(e) => setSecrets((s) => ({ ...s, email_credential: e.target.value }))} />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>Send a test email to</label>
            <input className="input" value={testEmailTo} onChange={(e) => setTestEmailTo(e.target.value)} placeholder="you@trip.example" />
          </div>
          <button className="btn btn--ghost btn--sm" onClick={sendTest}>Send test email</button>
        </div>
      </section>

      <section className="card" style={{ padding: 18, marginBottom: 14 }}>
        <div className="eyebrow">Trip.com integration</div>
        <div className="grid grid-2" style={{ gap: 12 }}>
          <Field label="Production base URL" value={form.trip.base_url} onChange={(v) => up('trip.base_url', v)} />
          <Field label="Sandbox base URL" value={form.trip.sandbox_base_url} onChange={(v) => up('trip.sandbox_base_url', v)} />
          <Field label="Client ID" value={form.trip.client_id} onChange={(v) => up('trip.client_id', v)} />
          <div className="field">
            <label>{SECRET_LABELS.trip_client_secret} <Fingerprint info={cfg.trip_client_secret} /></label>
            <input className="input" type="password" autoComplete="off"
                   value={secrets.trip_client_secret || ''} onChange={(e) => setSecrets((s) => ({ ...s, trip_client_secret: e.target.value }))} />
          </div>
          <div className="field">
            <label>{SECRET_LABELS.trip_webhook_secret} <Fingerprint info={cfg.trip_webhook_secret} /></label>
            <input className="input" type="password" autoComplete="off"
                   value={secrets.trip_webhook_secret || ''} onChange={(e) => setSecrets((s) => ({ ...s, trip_webhook_secret: e.target.value }))} />
          </div>
        </div>
      </section>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button className="btn" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save configuration'}</button>
      </div>
    </div>
  )
}
