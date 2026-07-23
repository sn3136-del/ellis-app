// Ellis in-app document preview (Phase 13). Loads bytes through a short-lived
// signed backend URL — never a Finder window, never a filesystem path, never a
// bucket URL. Supports images (zoom + rotate) and PDFs (native multi-page
// viewer in an iframe), with loading/error states and authorized download.
import { useEffect, useState } from 'react'
import { Loading } from '../ui.jsx'

export default function DocPreview({ client, caseId, doc, onClose }) {
  const [state, setState] = useState({ status: 'loading' })
  const [zoom, setZoom] = useState(1)
  const [rotation, setRotation] = useState(0)

  useEffect(() => {
    let alive = true
    setState({ status: 'loading' })
    client.documentPreviewUrl(caseId, doc.id)
      .then((r) => {
        if (!alive) return
        if (!r.available) setState({ status: 'unavailable', reason: r.reason })
        else setState({ status: 'ready', url: client.base + r.url, mime: r.mime })
      })
      .catch((e) => alive && setState({ status: 'error', message: e.message }))
    return () => { alive = false }
  }, [caseId, doc.id])

  const isPdf = state.mime === 'application/pdf'

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 900, width: '92%' }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <div style={{ fontWeight: 700, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{doc.name}</div>
          {state.status === 'ready' && !isPdf && (
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
              Couldn't load this document ({state.message}). The link may have expired — close and reopen the preview.
            </div>
          )}
          {state.status === 'ready' && (isPdf
            ? <iframe title={doc.name} src={state.url} style={{ width: '100%', height: '68vh', border: 0 }} />
            : <img src={state.url} alt={doc.name}
                   style={{ transform: `scale(${zoom}) rotate(${rotation}deg)`,
                            transformOrigin: 'center top', maxWidth: '100%',
                            transition: 'transform 120ms ease' }} />)}
        </div>
      </div>
    </div>
  )
}
