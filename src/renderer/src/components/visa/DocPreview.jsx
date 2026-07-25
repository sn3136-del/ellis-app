// Ellis in-app document preview (Phase 13). Fetches bytes through a
// short-lived signed backend URL and renders them from a local blob: URL —
// never a Finder window, never a filesystem path, never a bucket URL, and
// never a cross-origin frame (the page CSP forbids embedding third-party
// pages; blob: is the only frame/img source the preview uses). Supports
// images (zoom + rotate), PDFs (native multi-page viewer), and an honest
// fallback (name/type/size + download) for formats the browser cannot render.
import { useEffect, useState } from 'react'
import { Loading } from '../ui.jsx'

const RENDERABLE_IMAGE = /^image\/(jpeg|png|gif|webp)$/

function formatSize(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export default function DocPreview({ client, caseId, doc, onClose }) {
  const [state, setState] = useState({ status: 'loading' })
  const [zoom, setZoom] = useState(1)
  const [rotation, setRotation] = useState(0)

  useEffect(() => {
    let alive = true
    let objectUrl = null
    setState({ status: 'loading' })
    // Fresh signed URL every open (survives refresh/restart; links expire in
    // minutes), then authenticated-origin fetch → blob URL. The signed URL
    // itself never lands in the DOM.
    client.documentPreviewUrl(caseId, doc.id)
      .then(async (r) => {
        if (!alive) return
        if (!r.available) { setState({ status: 'unavailable', reason: r.reason }); return }
        const res = await fetch(client.base + r.url)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const blob = await res.blob()
        if (!alive) return
        objectUrl = URL.createObjectURL(blob)
        setState({ status: 'ready', url: objectUrl, mime: r.mime, size: blob.size })
      })
      .catch((e) => alive && setState({ status: 'error', message: e.message }))
    return () => {
      alive = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [caseId, doc.id])

  const isPdf = state.mime === 'application/pdf'
  const isImage = RENDERABLE_IMAGE.test(state.mime || '')
  const renderable = isPdf || isImage

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 900, width: '92%' }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <div style={{ fontWeight: 700, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{doc.name}</div>
          {state.status === 'ready' && isImage && (
            <>
              <button className="btn btn--ghost btn--sm" onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}>−</button>
              <span className="chip" style={{ fontSize: 11 }}>{Math.round(zoom * 100)}%</span>
              <button className="btn btn--ghost btn--sm" onClick={() => setZoom((z) => Math.min(4, z + 0.25))}>+</button>
              <button className="btn btn--ghost btn--sm" onClick={() => setRotation((r) => (r + 90) % 360)}>⟳ Rotate</button>
            </>
          )}
          {state.status === 'ready' && (
            <a className="btn btn--ghost btn--sm" href={state.url} download={doc.name}>Download</a>
          )}
          <button className="btn btn--sm" onClick={onClose}>Close</button>
        </div>

        <div style={{ background: 'var(--bg-2, #f4f4f4)', borderRadius: 10, minHeight: 320,
                      maxHeight: '70vh', overflow: 'auto', display: 'flex',
                      alignItems: 'flex-start', justifyContent: 'center' }}>
          {state.status === 'loading' && <div style={{ padding: 40 }}><Loading label="Loading document" /></div>}
          {state.status === 'unavailable' && (
            <div style={{ padding: 40, fontSize: 13, color: 'var(--muted)' }}>
              Preview unavailable: {state.reason}
            </div>
          )}
          {state.status === 'error' && (
            <div style={{ padding: 40, fontSize: 13, color: 'var(--crit)' }}>
              Couldn't load this document ({state.message}). Close and reopen the preview to try again.
            </div>
          )}
          {state.status === 'ready' && isPdf && (
            <iframe title={doc.name} src={state.url} style={{ width: '100%', height: '68vh', border: 0 }} />
          )}
          {state.status === 'ready' && isImage && (
            <img src={state.url} alt={doc.name}
                 style={{ transform: `scale(${zoom}) rotate(${rotation}deg)`,
                          transformOrigin: 'center top', maxWidth: '100%',
                          transition: 'transform 120ms ease' }} />
          )}
          {state.status === 'ready' && !renderable && (
            <div style={{ padding: 40, textAlign: 'center' }} data-testid="preview-fallback">
              <div style={{ fontWeight: 700, marginBottom: 4 }}>{doc.name}</div>
              <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 12 }}>
                {state.mime}{state.size ? ` · ${formatSize(state.size)}` : ''}
              </div>
              <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
                This file type can't be shown in the browser. Download it to view securely.
              </div>
              <a className="btn btn--sm" href={state.url} download={doc.name}>Download</a>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
