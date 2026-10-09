import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'
import { ErrorBox, Loading, Modal, btn } from './ui'

/** Fetches /api/documents/{id}/file with auth → object URL, and renders image/pdf/video/text inline. */
export function FilePreview({
  documentId,
  filename,
  mime,
  page,
  className = 'h-[70vh]',
}: {
  documentId: number
  filename?: string | null
  mime?: string | null
  page?: number | null
  className?: string
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [type, setType] = useState<string>('')
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    setLoading(true)
    setError(null)
    setUrl(null)
    setText(null)
    api
      .blob(`/documents/${documentId}/file`)
      .then(async ({ blob, type: t }) => {
        if (cancelled) return
        const guessed = guessType(filename, mime || t)
        const typed = blob.type === guessed ? blob : new Blob([blob], { type: guessed })
        if (guessed.startsWith('text/') || guessed === 'application/json') {
          const s = await typed.text()
          if (cancelled) return // unmounted/changed while reading: never create (and leak) an object URL
          setText(s)
        }
        objectUrl = URL.createObjectURL(typed)
        if (!cancelled) {
          setType(guessed)
          setUrl(objectUrl)
        }
      })
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [documentId, filename, mime])

  if (loading) return <Loading label="Loading original file…" />
  if (error)
    return (
      <div className="p-4">
        <ErrorBox error={error} />
      </div>
    )
  if (!url) return null

  const download = (
    <a href={url} download={filename || `document-${documentId}`} className={btn.secondary}>
      ⬇ Download {filename || 'file'}
    </a>
  )

  if (type.startsWith('image/'))
    return (
      <div className={`flex items-start justify-center overflow-auto bg-slate-100 p-2 ${className}`}>
        <img src={url} alt={filename || 'document'} className="max-w-full rounded shadow" />
      </div>
    )
  if (type === 'application/pdf')
    return <iframe title={filename || 'pdf'} src={page ? `${url}#page=${page}` : url} className={`w-full border-0 ${className}`} />
  if (type.startsWith('video/'))
    return (
      <div className={`flex items-center justify-center bg-black ${className}`}>
        <video src={url} controls className="max-h-full max-w-full" />
      </div>
    )
  if (text !== null)
    return (
      <div className={`overflow-auto bg-slate-50 ${className}`}>
        <div className="flex justify-end p-2">{download}</div>
        <pre className="whitespace-pre-wrap break-words px-4 pb-4 font-mono text-xs text-slate-700">{text}</pre>
      </div>
    )
  return (
    <div className={`flex flex-col items-center justify-center gap-3 bg-slate-50 p-6 text-center ${className}`}>
      <div className="text-4xl">📄</div>
      <div className="text-sm text-slate-600">
        Inline preview isn't available for this format ({type || 'unknown type'}).
      </div>
      {download}
    </div>
  )
}

function guessType(filename?: string | null, given?: string | null): string {
  const g = (given || '').split(';')[0].trim().toLowerCase()
  if (g && g !== 'application/octet-stream') return g
  const ext = (filename || '').split('.').pop()?.toLowerCase() || ''
  const map: Record<string, string> = {
    pdf: 'application/pdf',
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    webp: 'image/webp',
    gif: 'image/gif',
    mp4: 'video/mp4',
    webm: 'video/webm',
    mov: 'video/quicktime',
    txt: 'text/plain',
    csv: 'text/csv',
    md: 'text/plain',
  }
  return map[ext] || g || 'application/octet-stream'
}

// ---------- global preview modal ----------
interface PreviewTarget {
  id: number
  filename?: string | null
  page?: number | null
  snippet?: string | null
}
const PreviewCtx = createContext<(t: PreviewTarget) => void>(() => {})

export function PreviewProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<PreviewTarget | null>(null)
  const open = useCallback((t: PreviewTarget) => setTarget(t), [])
  const close = useCallback(() => setTarget(null), [])
  // A citation chip may not carry the filename (id missing from the response's citations): look it up.
  const missingName = target && !target.filename ? target.id : null
  useEffect(() => {
    if (missingName == null) return
    let cancelled = false
    api
      .get<{ filename?: string | null }>(`/documents/${missingName}`)
      .then((d) => {
        if (!cancelled && d?.filename) setTarget((t) => (t && t.id === missingName && !t.filename ? { ...t, filename: d.filename } : t))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [missingName])
  return (
    <PreviewCtx.Provider value={open}>
      {children}
      <Modal
        open={!!target}
        onClose={close}
        wide
        title={
          target && (
            <span>
              📎 {target.filename || 'Loading document…'}
              {target.page ? <span className="ml-2 text-slate-400">page {target.page}</span> : null}
            </span>
          )
        }
      >
        {target && (
          <div>
            {target.snippet && (
              <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
                <span className="font-semibold">Cited passage: </span>
                <span className="italic">“{target.snippet}”</span>
              </div>
            )}
            <FilePreview documentId={target.id} filename={target.filename} page={target.page} />
          </div>
        )}
      </Modal>
    </PreviewCtx.Provider>
  )
}

export const useDocPreview = () => useContext(PreviewCtx)

export function SourceLink({ docId, page, label, filename }: { docId?: number | null; page?: number | null; label?: string; filename?: string | null }) {
  const open = useDocPreview()
  if (!docId) return <span className="text-slate-300">—</span>
  return (
    <button
      onClick={() => open({ id: docId, page, filename })}
      className="inline-flex max-w-[12rem] items-center gap-1 truncate rounded-md bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-700 hover:bg-teal-100"
      title={filename ? `Open ${filename}` : 'Open source document'}
    >
      📎 <span className="truncate">{label || `${filename || `doc ${docId}`}${page ? ` · p${page}` : ''}`}</span>
    </button>
  )
}
