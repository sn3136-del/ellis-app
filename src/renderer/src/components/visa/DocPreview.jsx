// Ellis in-app document preview (Phase 13). Fetches bytes through a
// short-lived signed backend URL and renders them from a local blob: URL —
// never a Finder window, never a filesystem path, never a bucket URL, and
// never a cross-origin frame (the page CSP forbids embedding third-party
// pages; blob: is the only frame/img source the preview uses). Supports
// images and PDFs with center rotation (0/90/180/270 with a correctly swapped
// bounding box so nothing is ever clipped or covered), zoom and Reset, plain
// text (machine-translation artifacts), and an honest fallback
// (name/type/size + download) for formats the browser cannot render.
// Rotation is a viewing aid only — the stored document is never modified.
import { useEffect, useRef, useState } from 'react'
import { Loading, Overlay } from '../ui.jsx'
import { rotatedFrame } from '../../lib/intake.js'

const RENDERABLE_IMAGE = /^image\/(jpeg|png|gif|webp)$/

function formatSize(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// A rotated element inside a wrapper sized to its rotated bounding box: the
// wrapper participates in layout (scrolling works, nothing overlaps), the
// element rotates around its own center.
function RotatedBox({ w, h, rotation, zoom, maxW, children }) {
  const f = rotatedFrame({ w, h, rotation, zoom, maxW })
  if (!f.boxW) return null
  return (
    <div style={{ width: f.boxW, height: f.boxH, position: 'relative',
                  margin: '16px auto', flexShrink: 0 }}
      data-testid="preview-rotated-box" data-rotation={f.quarter}>
      <div style={{ position: 'absolute', top: '50%', left: '50%',
                    width: f.imgW, height: f.imgH,
                    transform: `translate(-50%, -50%) rotate(${f.quarter}deg)`,
                    transition: 'transform 120ms ease' }}>
        {children}
      </div>
    </div>
  )
}

export default function DocPreview({ client, caseId, doc, onClose }) {
  const [state, setState] = useState({ status: 'loading' })
  const [zoom, setZoom] = useState(1)
  const [rotation, setRotation] = useState(0)   // persists while the preview is open
  const [imgDims, setImgDims] = useState(null)  // natural {w, h}
  const [areaW, setAreaW] = useState(0)
  const areaRef = useRef(null)

  useEffect(() => {
    let alive = true
    let objectUrl = null
    setState({ status: 'loading' })
    setImgDims(null)
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
        if ((r.mime || '') === 'text/plain') {
          const text = await blob.text()
          objectUrl = URL.createObjectURL(blob)
          setState({ status: 'ready', url: objectUrl, mime: r.mime,
                     size: blob.size, text })
          return
        }
        objectUrl = URL.createObjectURL(blob)
        setState({ status: 'ready', url: objectUrl, mime: r.mime, size: blob.size })
      })
      .catch((e) => alive && setState({ status: 'error', message: e.message }))
    return () => {
      alive = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [caseId, doc.id])

  // Track the preview area's usable width so the rotated bounding box always
  // fits horizontally (vertical overflow scrolls).
  useEffect(() => {
    const el = areaRef.current
    if (!el) return
    const measure = () => setAreaW(Math.max(0, el.clientWidth - 32))
    measure()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    ro && ro.observe(el)
    return () => ro && ro.disconnect()
  }, [state.status])

  const isPdf = state.mime === 'application/pdf'
  const isImage = RENDERABLE_IMAGE.test(state.mime || '')
  const isText = state.mime === 'text/plain'
  const renderable = isPdf || isImage || isText
  const rotatable = isPdf || isImage
  const pristine = zoom === 1 && rotation === 0

  return (
    // Overlay portals to <body>: the backdrop is fixed to the VIEWPORT, never
    // to the animated card this preview is rendered from (see ui.jsx).
    <Overlay onClose={onClose}>
      <div className="modal" style={{ maxWidth: 900, width: 'min(900px, 92vw)' }}
        onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, flex: 1, minWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis' }}>{doc.name}</div>
          {state.status === 'ready' && rotatable && (
            <>
              <button className="btn btn--ghost btn--sm" onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}>−</button>
              <span className="chip" style={{ fontSize: 11 }}>{Math.round(zoom * 100)}%</span>
              <button className="btn btn--ghost btn--sm" onClick={() => setZoom((z) => Math.min(4, z + 0.25))}>+</button>
              <button className="btn btn--ghost btn--sm" data-testid="rotate"
                onClick={() => setRotation((r) => (r + 90) % 360)}>⟳ {rotation}°</button>
              {!pristine && (
                <button className="btn btn--ghost btn--sm" data-testid="reset-view"
                  onClick={() => { setZoom(1); setRotation(0) }}>Reset</button>
              )}
            </>
          )}
          {state.status === 'ready' && (
            <a className="btn btn--ghost btn--sm" href={state.url} download={doc.name}>Download</a>
          )}
          <button className="btn btn--sm" onClick={onClose}>Close</button>
        </div>

        <div ref={areaRef}
          style={{ background: 'var(--bg-2, #f4f4f4)', borderRadius: 10, minHeight: 320,
                   maxHeight: '70vh', overflow: 'auto', display: 'flex',
                   flexDirection: 'column', alignItems: 'center',
                   justifyContent: 'flex-start' }}>
          {state.status === 'loading' && <div style={{ padding: 40 }}><Loading label="Processing" /></div>}
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
            // PDF pages rotate wholesale (viewing aid only). Unrotated PDFs
            // keep the full-width native viewer.
            rotation === 0
              ? <iframe title={doc.name} src={state.url}
                  style={{ width: '100%', height: '68vh', border: 0,
                           transform: zoom === 1 ? undefined : `scale(${zoom})`,
                           transformOrigin: 'center top' }} />
              : <RotatedBox w={areaW || 800} h={Math.round((areaW || 800) * 0.75)}
                  rotation={rotation} zoom={zoom} maxW={areaW}>
                  <iframe title={doc.name} src={state.url}
                    style={{ width: '100%', height: '100%', border: 0 }} />
                </RotatedBox>
          )}
          {state.status === 'ready' && isImage && (
            imgDims
              ? <RotatedBox w={imgDims.w} h={imgDims.h} rotation={rotation}
                  zoom={zoom} maxW={areaW}>
                  <img src={state.url} alt={doc.name}
                       style={{ width: '100%', height: '100%' }} />
                </RotatedBox>
              : <img src={state.url} alt={doc.name} style={{ maxWidth: '100%', padding: 16 }}
                     onLoad={(e) => setImgDims({ w: e.target.naturalWidth,
                                                 h: e.target.naturalHeight })} />
          )}
          {state.status === 'ready' && isText && (
            <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                          fontSize: 13, lineHeight: 1.5, padding: 20, margin: 0,
                          width: '100%', boxSizing: 'border-box',
                          fontFamily: 'var(--font, inherit)' }}
              data-testid="text-preview">{state.text}</pre>
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
    </Overlay>
  )
}
