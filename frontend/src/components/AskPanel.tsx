import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { api, errorMessage, isConsentError } from '../api'
import type { AskResponse } from '../types'
import CitedMarkdown from './CitedMarkdown'
import { useDocPreview } from './DocumentPreview'
import { Pill, Spinner, arr } from './ui'

interface Msg {
  role: 'user' | 'assistant'
  content: string
  resp?: AskResponse
  error?: unknown
  ms?: number
  /** User turn whose ask failed — shown, but excluded from history sent to the model. */
  failed?: boolean
}

export function suggestedQuestions(sex?: string | null): string[] {
  const s = (sex || '').toUpperCase().startsWith('F') ? 'she' : (sex || '').toUpperCase().startsWith('M') ? 'he' : 'they'
  const is = s === 'they' ? 'are' : 'is'
  return [
    "Summarise this patient's diabetes control over 3 years",
    `What medications ${is} ${s} currently on and any interactions?`,
    'Any abnormal labs in the last 6 months?',
    'List all hospital admissions with discharge diagnoses',
  ]
}

const MAX_HISTORY = 20
const MAX_TURN_CHARS = 8000

const THINKING = ['Planning retrieval…', 'Querying structured records…', 'Reading source documents…', 'Composing a cited answer…']

function Thinking() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => setI((x) => Math.min(x + 1, THINKING.length - 1)), 1600)
    return () => window.clearInterval(t)
  }, [])
  return (
    <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-500 shadow-sm">
      <Spinner /> {THINKING[i]}
    </div>
  )
}

function AssistantMessage({ m }: { m: Msg }) {
  const open = useDocPreview()
  const [showTools, setShowTools] = useState(false)
  if (m.error) {
    const consent = isConsentError(m.error)
    return (
      <div className={`rounded-xl border px-3 py-2.5 text-sm ${consent ? 'border-amber-300 bg-amber-50 text-amber-900' : 'border-rose-200 bg-rose-50 text-rose-800'}`}>
        {consent ? '🔒 ' : '⚠ '}
        {errorMessage(m.error)}
      </div>
    )
  }
  const r = m.resp!
  const cites = arr(r.citations)
  const tools = arr(r.tool_calls)
  const warnings = arr(r.warnings)
  // De-duplicate citations by doc+page for the sources strip.
  const uniq = cites.filter((c, i) => cites.findIndex((x) => x.document_id === c.document_id && (x.page ?? null) === (c.page ?? null)) === i)
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-1.5">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-600">
          <span className="flex h-5 w-5 items-center justify-center rounded-md bg-teal-600 text-[10px] text-white">AI</span>
          Clinical assistant
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          {m.ms !== undefined && <span>{(m.ms / 1000).toFixed(1)}s</span>}
          <Pill value={r.mode === 'claude' ? 'claude' : 'offline'} label={r.mode === 'claude' ? 'Claude' : 'Offline'} />
        </div>
      </div>
      {warnings.length > 0 && (
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-900">
          {warnings.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      )}
      <div className="px-3 py-2.5">
        <CitedMarkdown markdown={r.answer || '_No answer returned._'} citations={cites} />
      </div>
      {uniq.length > 0 && (
        <div className="border-t border-slate-100 px-3 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Sources ({uniq.length})</div>
          <div className="flex flex-wrap gap-1.5">
            {uniq.map((c, i) => (
              <button
                key={i}
                onClick={() => open({ id: c.document_id, filename: c.filename, page: c.page, snippet: c.snippet })}
                className="group max-w-full rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-left hover:border-teal-300 hover:bg-teal-50"
                title={c.snippet || ''}
              >
                <div className="truncate text-[11px] font-medium text-slate-700 group-hover:text-teal-800">
                  📎 {c.filename || `Document ${c.document_id}`}
                  {c.page ? <span className="text-slate-400"> · p{c.page}</span> : null}
                </div>
                {c.snippet && <div className="line-clamp-1 max-w-[16rem] text-[10px] italic text-slate-500">“{c.snippet}”</div>}
              </button>
            ))}
          </div>
        </div>
      )}
      {tools.length > 0 && (
        <div className="border-t border-slate-100">
          <button onClick={() => setShowTools((s) => !s)} className="flex w-full items-center justify-between px-3 py-1.5 text-[11px] font-medium text-slate-500 hover:bg-slate-50">
            <span>🔎 How I found this — {tools.length} tool call{tools.length > 1 ? 's' : ''}</span>
            <span>{showTools ? '▴' : '▾'}</span>
          </button>
          {showTools && (
            <ol className="space-y-1 px-3 pb-2">
              {tools.map((t, i) => (
                <li key={i} className="flex items-start gap-2 rounded-md bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-slate-100">
                  <span className="text-slate-500">{i + 1}.</span>
                  <span className="min-w-0 flex-1 break-all">
                    <span className="text-teal-300">{t.name}</span>
                    <span className="text-slate-400">({t.args && Object.keys(t.args).length ? JSON.stringify(t.args) : ''})</span>
                  </span>
                  {typeof t.row_count === 'number' && <span className="shrink-0 rounded bg-slate-700 px-1.5 text-slate-200">{t.row_count} rows</span>}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  )
}

export default function AskPanel({
  patientId,
  patientName,
  sex,
  onConsentLost,
}: {
  patientId: number
  patientName?: string
  sex?: string | null
  onConsentLost?: () => void
}) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const suggestions = suggestedQuestions(sex)

  // Every ask gets an id; switching patient, clearing, or unmounting bumps it so a late
  // answer for a previous patient/conversation is dropped instead of rendered.
  const reqId = useRef(0)
  useEffect(() => {
    reqId.current++
    setMsgs([])
    setBusy(false)
    return () => {
      reqId.current++
    }
  }, [patientId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [msgs, busy])

  const ask = async (question: string) => {
    const text = question.trim()
    if (!text || busy) return
    // History = successful turns only (failed user turns and error bubbles excluded), so it
    // always alternates user/assistant.
    // Bounded: the last MAX_HISTORY turns, starting on a user turn, each truncated to MAX_TURN_CHARS.
    let history = msgs
      .filter((m) => !m.error && !m.failed)
      .map((m) => ({ role: m.role, content: m.content.slice(0, MAX_TURN_CHARS) }))
      .slice(-MAX_HISTORY)
    while (history.length && history[0].role !== 'user') history = history.slice(1)
    const userMsg: Msg = { role: 'user', content: text }
    setMsgs((m) => [...m, userMsg])
    setQ('')
    setBusy(true)
    const my = ++reqId.current
    const t0 = performance.now()
    try {
      const r = await api.post<AskResponse>(`/doctor/patients/${patientId}/ask`, { question: text, history })
      if (my !== reqId.current) return
      setMsgs((m) => [...m, { role: 'assistant', content: r?.answer || '', resp: r || { answer: '' }, ms: performance.now() - t0 }])
    } catch (e) {
      if (my !== reqId.current) return
      setMsgs((m) => [...m.map((x) => (x === userMsg ? { ...x, failed: true } : x)), { role: 'assistant', content: '', error: e }])
      if (isConsentError(e)) onConsentLost?.()
    } finally {
      if (my === reqId.current) setBusy(false)
    }
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      ask(q)
    }
  }

  return (
    <div className="flex h-full min-h-[480px] flex-col overflow-hidden rounded-xl border border-teal-200 bg-gradient-to-b from-teal-50/60 to-white shadow-sm">
      <div className="flex items-center justify-between gap-2 border-b border-teal-100 bg-white/80 px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-slate-800">Ask about {patientName?.split(' ')[0] || 'this patient'}</div>
          <div className="text-[11px] text-slate-500">Answers come only from this patient's records — every claim is cited.</div>
        </div>
        {msgs.length > 0 && (
          <button onClick={() => setMsgs([])} className="text-xs text-slate-400 hover:text-slate-600" disabled={busy}>
            Clear
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {msgs.length === 0 && (
          <div className="space-y-3 py-2">
            <div className="text-center text-sm text-slate-500">
              <div className="mb-1 text-3xl">💬</div>
              Skip the 300 pages. Ask a question:
            </div>
            <div className="grid gap-2">
              {suggestions.map((s) => (
                <button key={s} onClick={() => ask(s)} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left text-sm text-slate-700 shadow-sm transition hover:border-teal-400 hover:bg-teal-50">
                  <span className="mr-1 text-teal-600">↳</span> {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-teal-600 px-3 py-2 text-sm text-white shadow-sm">{m.content}</div>
            </div>
          ) : (
            <AssistantMessage key={i} m={m} />
          ),
        )}
        {busy && <Thinking />}
        <div ref={endRef} />
      </div>

      {msgs.length > 0 && !busy && (
        <div className="flex gap-1.5 overflow-x-auto border-t border-teal-100 px-3 py-2">
          {suggestions.map((s) => (
            <button key={s} onClick={() => ask(s)} className="shrink-0 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-600 hover:border-teal-400 hover:text-teal-700">
              {s}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex items-end gap-2 border-t border-teal-100 bg-white p-3"
        onSubmit={(e) => {
          e.preventDefault()
          ask(q)
        }}
      >
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          rows={2}
          placeholder="Ask anything — e.g. When was the last creatinine, and was it normal?"
          className="max-h-40 min-h-[44px] flex-1 resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
          disabled={busy}
        />
        <button className="inline-flex h-11 items-center justify-center rounded-lg bg-teal-600 px-4 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-50" disabled={busy || !q.trim()}>
          {busy ? <Spinner className="h-4 w-4 text-white" /> : 'Ask'}
        </button>
      </form>
    </div>
  )
}
