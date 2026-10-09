import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import { api } from '../../api'
import { useAuth } from '../../auth'
import { FilePreview, useDocPreview } from '../../components/DocumentPreview'
import ReviewForm, { cleanExtraction, normaliseExtraction } from '../../components/ReviewForm'
import { Card, Empty, ErrorBox, Loading, PageHeader, Pill, Spinner, aiBrand, btn, fmtBytes, fmtDate, titleCase } from '../../components/ui'
import type { DocumentRow, ExtractionResult, UploadResponse } from '../../types'

const ACCEPT = '.pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.webp,.mp4,.mov,.webm,.avi,.txt,.csv'
const DEFAULT_MAX_MB = 20 // fallback until /api/system/info has loaded

const KIND_ICON: Record<string, string> = {
  pdf: '📕',
  docx: '📘',
  doc: '📘',
  xlsx: '📗',
  xls: '📗',
  image: '🖼',
  video: '🎞',
  text: '📄',
}

const STEPS = ['queued', 'processing', 'needs_review', 'committed'] as const

function Progress({ status }: { status: DocumentRow['status'] }) {
  if (status === 'failed') return <Pill value="failed" />
  const idx = STEPS.indexOf(status as (typeof STEPS)[number])
  return (
    <div className="flex items-center gap-1" title={titleCase(status)}>
      {STEPS.map((s, i) => (
        <div key={s} className={`h-1.5 w-6 rounded-full ${i <= idx ? (status === 'needs_review' && i === idx ? 'bg-amber-400' : 'bg-teal-500') : 'bg-slate-200'}`} />
      ))}
      <Pill value={status} className="ml-2" label={status === 'needs_review' ? 'Needs review' : undefined} />
    </div>
  )
}

export default function Upload() {
  const { systemInfo } = useAuth()
  const MAX_MB = systemInfo?.max_upload_mb || DEFAULT_MAX_MB
  const [docs, setDocs] = useState<DocumentRow[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [uploading, setUploading] = useState(false)
  const [rejected, setRejected] = useState<{ filename: string; reason: string }[]>([])
  const [drag, setDrag] = useState(false)
  const [reviewId, setReviewId] = useState<number | null>(null)
  const [draft, setDraft] = useState<ExtractionResult | null>(null)
  const [committing, setCommitting] = useState(false)
  const [actionErr, setActionErr] = useState<unknown>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const openPreview = useDocPreview()

  const load = useCallback(async () => {
    try {
      const d = await api.get<DocumentRow[]>('/documents')
      setDocs(Array.isArray(d) ? d : [])
      setError(null)
    } catch (e) {
      setError(e)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Poll every 2s while anything is queued/processing.
  const pending = (docs || []).some((d) => d.status === 'queued' || d.status === 'processing')
  useEffect(() => {
    if (!pending) return
    const t = window.setInterval(load, 2000)
    return () => window.clearInterval(t)
  }, [pending, load])

  const upload = async (files: File[]) => {
    if (!files.length) return
    setUploading(true)
    setActionErr(null)
    // Client-side size guard (server also enforces); oversize files are rejected individually.
    const tooBig = files.filter((f) => f.size > MAX_MB * 1024 * 1024)
    const ok = files.filter((f) => f.size <= MAX_MB * 1024 * 1024)
    const localRejects = tooBig.map((f) => ({ filename: f.name, reason: `File exceeds ${MAX_MB} MB` }))
    try {
      if (ok.length) {
        const r = await api.upload<UploadResponse>('/documents', ok, 'files')
        setRejected([...localRejects, ...(r?.rejected || [])])
        const n = r?.accepted?.length ?? 0
        setFlash(n ? `${n} file${n > 1 ? 's' : ''} queued for digitisation` : null)
      } else {
        setRejected(localRejects)
      }
      await load()
    } catch (e) {
      setRejected(localRejects)
      setActionErr(e)
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDrag(false)
    upload(Array.from(e.dataTransfer.files || []))
  }

  const startReview = (d: DocumentRow) => {
    setReviewId(d.id)
    setDraft(normaliseExtraction(d.extraction))
    setActionErr(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const doAction = async (id: number, fn: () => Promise<unknown>) => {
    setBusyId(id)
    setActionErr(null)
    try {
      await fn()
      await load()
    } catch (e) {
      setActionErr(e)
    } finally {
      setBusyId(null)
    }
  }

  const commit = async () => {
    if (!reviewId || !draft) return
    setCommitting(true)
    setActionErr(null)
    try {
      await api.post(`/documents/${reviewId}/commit`, { extraction: cleanExtraction(draft) })
      setFlash('Committed to your health record ✓')
      setReviewId(null)
      setDraft(null)
      await load()
    } catch (e) {
      setActionErr(e)
    } finally {
      setCommitting(false)
    }
  }

  const reviewing = reviewId !== null ? (docs || []).find((d) => d.id === reviewId) : undefined
  const needsReview = (docs || []).filter((d) => d.status === 'needs_review')

  useEffect(() => {
    if (!flash) return
    const t = window.setTimeout(() => setFlash(null), 4000)
    return () => window.clearTimeout(t)
  }, [flash])

  // ---------- review mode ----------
  if (reviewId !== null && draft) {
    return (
      <div>
        <PageHeader
          title={
            <span className="flex items-center gap-2">
              <button onClick={() => setReviewId(null)} className="rounded-md px-2 py-1 text-base text-slate-500 hover:bg-slate-100" title="Back">
                ←
              </button>
              Review extraction
            </span>
          }
          subtitle={
            reviewing ? (
              <span>
                {reviewing.filename} · digitised by{' '}
                <Pill value={aiBrand(reviewing.provider) ? 'claude' : reviewing.provider || 'offline'} label={aiBrand(reviewing.provider) ?? 'Offline OCR'} /> — correct anything that's wrong, then commit.
              </span>
            ) : undefined
          }
          action={
            <div className="flex gap-2">
              <button className={btn.secondary} onClick={() => setReviewId(null)}>
                Cancel
              </button>
              <button className={btn.primary} onClick={commit} disabled={committing}>
                {committing && <Spinner className="h-4 w-4 text-white" />} ✓ Commit to record
              </button>
            </div>
          }
        />
        <ErrorBox error={actionErr} />
        <div className="mt-3 grid gap-4 xl:grid-cols-2">
          <Card title="Original file" bodyClass="p-0" className="xl:sticky xl:top-20 xl:self-start">
            <FilePreview documentId={reviewId} filename={reviewing?.filename} mime={reviewing?.mime} className="h-[45vh] xl:h-[calc(100vh-12rem)]" />
          </Card>
          <Card title="Extracted fields (editable)">
            <ReviewForm key={reviewId} value={draft} onChange={setDraft} />
            <div className="mt-5 flex justify-end gap-2 border-t border-slate-100 pt-4">
              <button className={btn.secondary} onClick={() => setReviewId(null)}>
                Cancel
              </button>
              <button className={btn.primary} onClick={commit} disabled={committing}>
                {committing && <Spinner className="h-4 w-4 text-white" />} ✓ Commit to record
              </button>
            </div>
          </Card>
        </div>
      </div>
    )
  }

  // ---------- list mode ----------
  return (
    <div>
      <PageHeader title="Upload & Digitise" subtitle="Drop any clinical file — AI extracts medications, labs, vitals and visits for you to review." />

      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition ${
          drag ? 'border-teal-500 bg-teal-50' : 'border-slate-300 bg-white hover:border-teal-400 hover:bg-teal-50/40'
        }`}
      >
        <input ref={inputRef} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => upload(Array.from(e.target.files || []))} />
        {uploading ? <Spinner className="h-8 w-8" /> : <div className="text-4xl">⬆</div>}
        <div className="text-base font-semibold text-slate-800">{uploading ? 'Uploading…' : 'Drag & drop files here, or click to browse'}</div>
        <div className="text-xs text-slate-500">PDF · DOC/DOCX · XLS/XLSX · JPG/PNG (incl. handwritten) · MP4/MOV video · TXT/CSV — up to {MAX_MB} MB each</div>
      </div>

      <div className="mt-4 space-y-3">
        {flash && <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">{flash}</div>}
        {rejected.length > 0 && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800">
            <div className="flex items-center justify-between">
              <span className="font-semibold">Some files were rejected (others continued):</span>
              <button className="text-xs underline" onClick={() => setRejected([])}>
                dismiss
              </button>
            </div>
            <ul className="mt-1 list-disc pl-5 text-xs">
              {rejected.map((r, i) => (
                <li key={i}>
                  <span className="font-medium">{r.filename}</span> — {r.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
        <ErrorBox error={actionErr} />
        {needsReview.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
            <span>
              <b>{needsReview.length}</b> document{needsReview.length > 1 ? 's' : ''} ready for your review.
            </span>
            <button className={btn.primary} onClick={() => startReview(needsReview[0])}>
              Review next →
            </button>
          </div>
        )}
      </div>

      <Card
        className="mt-4"
        title={
          <span className="flex items-center gap-2">
            Your documents {docs && <span className="text-slate-400">({docs.length})</span>}
            {pending && (
              <span className="flex items-center gap-1 text-xs font-normal text-sky-600">
                <Spinner className="h-3 w-3" /> processing… refreshing every 2s
              </span>
            )}
          </span>
        }
        bodyClass="p-0"
      >
        {!docs && !error && <Loading />}
        {error ? (
          <div className="p-4">
            <ErrorBox error={error} onRetry={load} />
          </div>
        ) : null}
        {docs && docs.length === 0 && (
          <div className="p-4">
            <Empty>No documents yet — upload your first file above.</Empty>
          </div>
        )}
        {docs && docs.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {docs.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="text-2xl" aria-hidden>
                  {KIND_ICON[d.kind || ''] || '📄'}
                </div>
                <div className="min-w-0 flex-1">
                  <button onClick={() => openPreview({ id: d.id, filename: d.filename })} className="block max-w-full truncate text-left text-sm font-medium text-slate-800 hover:text-teal-700 hover:underline">
                    {d.filename}
                  </button>
                  <div className="text-xs text-slate-500">
                    {[d.kind?.toUpperCase(), fmtBytes(d.size), d.doc_type ? titleCase(d.doc_type) : null, d.document_date ? `dated ${fmtDate(d.document_date)}` : null, `uploaded ${fmtDate(d.uploaded_at, true)}`]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                  {d.status === 'failed' && d.error && <div className="mt-1 text-xs text-rose-600">✕ {d.error}</div>}
                  {d.extraction?.summary && d.status !== 'failed' && <div className="mt-1 line-clamp-1 text-xs italic text-slate-500">{d.extraction.summary}</div>}
                </div>
                <Progress status={d.status} />
                <div className="flex items-center gap-1">
                  {busyId === d.id && <Spinner />}
                  {d.status === 'needs_review' && (
                    <button className={btn.primary + ' !px-3 !py-1.5'} onClick={() => startReview(d)}>
                      Review
                    </button>
                  )}
                  {(d.status === 'failed' || d.status === 'needs_review') && (
                    <button className={btn.ghost} onClick={() => doAction(d.id, () => api.post(`/documents/${d.id}/reprocess`))} title="Run digitisation again">
                      ↻ Reprocess
                    </button>
                  )}
                  {d.status !== 'committed' && d.status !== 'processing' && (
                    <button
                      className="rounded-md px-2 py-1 text-sm text-rose-600 hover:bg-rose-50"
                      onClick={() => window.confirm(`Delete ${d.filename}?`) && doAction(d.id, () => api.del(`/documents/${d.id}`))}
                      title="Delete"
                    >
                      🗑
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
