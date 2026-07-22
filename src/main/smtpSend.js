import { createTransport } from 'nodemailer'
import { existsSync, readFileSync } from 'fs'
import { basename } from 'path'
import { getState } from './store.js'

// Outbound sender for traveler emails. The org enters its own internal
// address + an SMTP app password in Settings; when both are present, mail is
// sent from that address. When they aren't, delivery falls back to the local
// macOS Mail account. No personal address is baked into the build.
const DEFAULT_HOST = 'smtp.gmail.com'
const DEFAULT_PORT = 587

export function smtpConfig() {
  const s = getState().settings?.smtp || {}
  const user = String(s.user || process.env.ELLIS_SMTP_USER || '').trim()
  const pass = String(s.appPassword || process.env.ELLIS_SMTP_PASS || '').replace(/\s+/g, '')
  const host = String(s.host || process.env.ELLIS_SMTP_HOST || DEFAULT_HOST).trim()
  const port = Number(s.port || process.env.ELLIS_SMTP_PORT || DEFAULT_PORT) || DEFAULT_PORT
  return { user, pass, host, port, configured: !!(user && pass) }
}

function bodyToHtml(text) {
  const esc = String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // **emphasis** renders as real bold in the HTML part — never literal
    // asterisks in the traveler's inbox.
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
  const blocks = esc.split(/\n\n+/).map((block) => {
    const inner = block.replace(/\n/g, '<br>\n')
    return `<p style="margin:0 0 16px 0;line-height:1.55;font-size:15px;color:#1a1a1a;">${inner}</p>`
  })
  return `<!DOCTYPE html><html><body style="margin:0;padding:20px 4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">${blocks.join('\n')}</body></html>`
}

export async function sendViaSmtp({ to, subject, body, attachmentPath, attachmentPaths }) {
  const cfg = smtpConfig()
  if (!cfg.configured) {
    return { ok: false, error: 'NO_SMTP', needSetup: true }
  }
  const recipient = to || cfg.user
  const transporter = createTransport({
    host: cfg.host,
    port: cfg.port,
    secure: cfg.port === 465,
    auth: { user: cfg.user, pass: cfg.pass }
  })

  const raw = body || ''
  const mail = {
    from: `"Trip.com Visa" <${cfg.user}>`,
    to: recipient,
    subject: subject || '(no subject)',
    // Plain-text alternative gets the asterisks stripped; the HTML part
    // carries the actual bold.
    text: raw.replace(/\*\*([^*\n]+)\*\*/g, '$1'),
    html: bodyToHtml(raw)
  }
  const paths = [...(Array.isArray(attachmentPaths) ? attachmentPaths : []), attachmentPath]
    .filter((p) => p && existsSync(p))
    .filter((p, i, arr) => arr.indexOf(p) === i)
  if (paths.length) {
    mail.attachments = paths.map((p) => ({ filename: basename(p), content: readFileSync(p) }))
  }

  try {
    const info = await transporter.sendMail(mail)
    return { ok: true, to: recipient, from: cfg.user, messageId: info.messageId, via: 'smtp' }
  } catch (err) {
    const msg = String(err?.message || err)
    return {
      ok: false,
      error: msg.slice(0, 400),
      needSetup: /Invalid login|Username and Password|BadCredentials|535|534|EAUTH/i.test(msg),
      via: 'smtp'
    }
  }
}
