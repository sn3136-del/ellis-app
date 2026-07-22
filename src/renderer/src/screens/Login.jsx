import { useState } from 'react'
import { ROLES } from '../lib/api.js'
import { Icon } from '../components/icons.jsx'
import { wordmarkInk } from '../assets/logos.js'

export default function Login({ role, profile, onAuth, onBack }) {
  const ctx = !profile ? ''
    : role === 'tripcom' ? 'Global tourist visas · every destination'
    : role === 'immigrant' ? `${profile.originCountry} → ${profile.destinationCountry} · ${profile.pathway}`
    : `${profile.destinationCountry} · ${profile.jurisdiction === 'Canada' ? 'IRCC' : 'USCIS'}`
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  function submit(e) {
    e?.preventDefault()
    if (username.trim() === 'admin' && password === '1234') { setError(''); onAuth() }
    else setError('Incorrect username or password.')
  }

  return (
    <div className="roles-screen">
      <div style={{ width: '100%', maxWidth: 380 }} className="roles-wrap">
        <img className="roles-logo" src={wordmarkInk} alt="Ellis" />
        <h1 className="h-serif" style={{ fontSize: 30, marginTop: 4, marginBottom: 4 }}>Sign in</h1>
        <p style={{ color: 'var(--muted)', fontSize: 14, marginBottom: 24 }}>{ROLES[role].label} workspace{ctx ? ` · ${ctx}` : ''}</p>

        <form onSubmit={submit} style={{ textAlign: 'left' }}>
          <div className="field"><label>Username</label>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" autoFocus />
          </div>
          <div className="field"><label>Password</label>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••" />
          </div>
          {error && <div style={{ color: 'var(--ink)', fontSize: 13, marginBottom: 12, fontWeight: 600 }}>{error}</div>}
          <button className="btn btn--block" type="submit">Sign in <Icon.arrow style={{ width: 16, height: 16 }} /></button>
          <button className="btn btn--ghost btn--block" type="button" style={{ marginTop: 10 }} onClick={onBack}>Back</button>
        </form>
      </div>
    </div>
  )
}
