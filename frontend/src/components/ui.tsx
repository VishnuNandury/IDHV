import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { errorMessage, isConsentError } from '../api'

// ---------- formatting ----------
/**
 * Parse an API timestamp. The backend stores naive UTC and serialises it without an offset
 * ("2026-10-01T10:00:00"), which browsers would read as *local* time — so append "Z" when no
 * zone is present. Date-only strings ("2026-10-01") are treated as local calendar dates.
 */
// Time-zone designator at the end of an ISO datetime (only tested on strings that have a time part).
const TZ_SUFFIX = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i

export function parseTs(s?: string | null): Date | null {
  if (!s) return null
  const str = String(s).trim()
  let dt: Date
  if (/^\d{4}-\d{2}-\d{2}$/.test(str)) dt = new Date(str + 'T00:00:00')
  else if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(str)) {
    // Already zoned ("…Z", "…+00:00", "…+0530", "…+05") → parse as-is (never shift twice);
    // naive → it's UTC from the backend, so append Z.
    const iso = str.replace(' ', 'T')
    dt = new Date(TZ_SUFFIX.test(iso) ? iso : iso + 'Z')
  } else dt = new Date(str)
  return isNaN(dt.getTime()) ? null : dt
}

/** Milliseconds until a timestamp (negative if past), or null if unparseable. */
export function msUntil(s: string | null | undefined, now: number): number | null {
  const d = parseTs(s)
  return d ? d.getTime() - now : null
}

export function localToday(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function fmtDate(d?: string | null, withTime = false): string {
  if (!d) return '—'
  const dt = parseTs(d) || new Date(NaN)
  if (isNaN(dt.getTime())) return d
  return withTime
    ? dt.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function fmtINR(n?: number | string | null): string {
  if (n === null || n === undefined || n === '') return '—'
  const v = typeof n === 'number' ? n : Number(n)
  if (isNaN(v)) return String(n)
  return '₹' + v.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

export function fmtBytes(n?: number | null): string {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function titleCase(s?: string | null): string {
  if (!s) return '—'
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function ageFrom(dob?: string | null): number | null {
  if (!dob) return null
  const d = new Date(dob)
  if (isNaN(d.getTime())) return null
  const now = new Date()
  let a = now.getFullYear() - d.getFullYear()
  const m = now.getMonth() - d.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < d.getDate())) a--
  return a
}

export const arr = <T,>(x: T[] | null | undefined): T[] => (Array.isArray(x) ? x : [])

// ---------- hooks ----------
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const seq = useRef(0)

  const reload = useCallback(async () => {
    const my = ++seq.current
    setLoading(true)
    setError(null)
    try {
      const r = await fnRef.current()
      if (my === seq.current) setData(r)
    } catch (e) {
      if (my === seq.current) setError(e)
    } finally {
      if (my === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, error, loading, reload, setData }
}

export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(t)
  }, [intervalMs])
  return now
}

// ---------- primitives ----------
export function Card({
  title,
  action,
  children,
  className = '',
  bodyClass = 'p-4',
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  bodyClass?: string
}) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
          {action}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  )
}

export function PageHeader({ title, subtitle, action }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900 sm:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg className={`animate-spin text-teal-600 ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-20" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
      <Spinner /> {label}
    </div>
  )
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null
  const consent = isConsentError(error)
  return (
    <div
      className={`flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm ${
        consent ? 'border-amber-300 bg-amber-50 text-amber-900' : 'border-rose-200 bg-rose-50 text-rose-800'
      }`}
      role="alert"
    >
      <div className="flex items-start gap-2">
        <span aria-hidden>{consent ? '🔒' : '⚠'}</span>
        <span>{errorMessage(error)}</span>
      </div>
      {onRetry && !consent && (
        <button onClick={onRetry} className="shrink-0 font-medium underline">
          Retry
        </button>
      )}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-200 p-6 text-center text-sm text-slate-400">{children}</div>
}

const PILL: Record<string, string> = {
  queued: 'bg-slate-100 text-slate-600',
  processing: 'bg-sky-100 text-sky-700',
  needs_review: 'bg-amber-100 text-amber-800',
  committed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-rose-100 text-rose-700',
  pending: 'bg-amber-100 text-amber-800',
  active: 'bg-emerald-100 text-emerald-700',
  expired: 'bg-slate-100 text-slate-500',
  revoked: 'bg-rose-100 text-rose-700',
  resolved: 'bg-slate-100 text-slate-500',
  high: 'bg-rose-100 text-rose-700',
  low: 'bg-sky-100 text-sky-700',
  normal: 'bg-emerald-100 text-emerald-700',
  patient: 'bg-teal-100 text-teal-700',
  doctor: 'bg-indigo-100 text-indigo-700',
  admin: 'bg-slate-800 text-white',
  claude: 'bg-violet-100 text-violet-700',
  offline: 'bg-slate-200 text-slate-700',
}

export function Pill({ value, label, className = '' }: { value?: string | null; label?: ReactNode; className?: string }) {
  if (!value && !label) return null
  const cls = PILL[(value || '').toLowerCase()] || 'bg-slate-100 text-slate-600'
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${cls} ${className}`}>
      {value === 'processing' && <Spinner className="h-3 w-3" />}
      {label ?? titleCase(value)}
    </span>
  )
}

export const btn = {
  primary:
    'inline-flex items-center justify-center gap-2 rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50',
  secondary:
    'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50',
  danger:
    'inline-flex items-center justify-center gap-2 rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-sm font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50',
  ghost: 'inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm text-teal-700 hover:bg-teal-50',
}

export const inputCls =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-500/20'

export function Field({ label, children, className = '' }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  )
}

export function Stat({ label, value, sub, tone = 'teal' }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'teal' | 'rose' | 'amber' | 'slate' | 'indigo' }) {
  const tones: Record<string, string> = {
    teal: 'text-teal-700',
    rose: 'text-rose-600',
    amber: 'text-amber-600',
    slate: 'text-slate-800',
    indigo: 'text-indigo-700',
  }
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${tones[tone]}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: ReactNode }[]
  value: T
  onChange: (t: T) => void
}) {
  return (
    <div className="mb-4 flex gap-1 overflow-x-auto border-b border-slate-200">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium transition ${
            value === t.id ? 'border-teal-600 text-teal-700' : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  wide?: boolean
}) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-2 sm:p-6" onClick={onClose}>
      <div
        className={`flex max-h-full w-full flex-col overflow-hidden rounded-xl bg-white shadow-2xl ${wide ? 'max-w-5xl' : 'max-w-lg'}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0 truncate text-sm font-semibold text-slate-800">{title}</div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
      </div>
    </div>
  )
}
