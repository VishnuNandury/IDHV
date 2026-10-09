import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { homeFor, useAuth } from '../auth'
import { AiBadge, DemoBanner, Logo } from '../components/Layout'
import { ErrorBox, Field, Spinner, btn, inputCls } from '../components/ui'

const DEMO = [
  { email: 'ananya@demo.in', name: 'Ananya Sharma', role: 'Patient', note: 'T2DM + hypertension · 3 yrs of records', icon: '👩' },
  { email: 'ravi@demo.in', name: 'Ravi Kumar', role: 'Patient', note: 'Asthma · active grant to Dr. Meera', icon: '👨' },
  { email: 'meera@demo.in', name: 'Dr. Meera Iyer', role: 'Doctor', note: 'Cardiologist / Physician', icon: '🩺' },
  { email: 'admin@demo.in', name: 'Admin', role: 'Admin', note: 'Audit log & stats', icon: '🛡' },
]

export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-gradient-to-br from-teal-50 via-white to-slate-100">
      <DemoBanner />
      <div className="flex items-center justify-between px-4 py-3 sm:px-8">
        <Logo />
        <AiBadge />
      </div>
      <div className="flex flex-1 items-center justify-center px-4 py-8">{children}</div>
    </div>
  )
}

export default function Login() {
  const { user, login } = useAuth()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  if (user) return <Navigate to={homeFor(user.role)} replace />

  const go = async (e: string, p: string) => {
    setBusy(e)
    setError(null)
    try {
      const u = await login(e, p)
      nav(homeFor(u.role), { replace: true })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  const submit = (ev: FormEvent) => {
    ev.preventDefault()
    go(email.trim(), password)
  }

  return (
    <AuthShell>
      <div className="grid w-full max-w-5xl gap-8 lg:grid-cols-2">
        <div className="hidden flex-col justify-center lg:flex">
          <h1 className="text-4xl font-bold leading-tight text-slate-900">
            One vault for every record.
            <br />
            <span className="text-teal-600">Answers, not paperwork.</span>
          </h1>
          <p className="mt-4 max-w-md text-slate-600">
            Patients upload prescriptions, lab reports, discharge summaries — even handwritten notes and videos. AI digitises them
            into a structured record. Doctors get time-bound, OTP-consented access and ask questions in plain language, with
            citations back to the source page.
          </p>
          <ul className="mt-6 space-y-2 text-sm text-slate-600">
            {['PDF · DOCX · XLSX · JPG · handwritten · video', 'Patient-controlled consent with OTP / QR, revocable anytime', 'Every answer cites its source document', 'Tamper-evident, hash-chained audit trail'].map((t) => (
              <li key={t} className="flex items-center gap-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-teal-100 text-xs text-teal-700">✓</span>
                {t}
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xl sm:p-8">
          <h2 className="text-xl font-semibold text-slate-900">Sign in</h2>
          <p className="mt-1 text-sm text-slate-500">Use a one-click demo account or your own.</p>

          <div className="mt-5 grid gap-2 sm:grid-cols-2">
            {DEMO.map((d) => (
              <button
                key={d.email}
                onClick={() => go(d.email, 'demo1234')}
                disabled={!!busy}
                className="group flex items-start gap-3 rounded-xl border border-slate-200 p-3 text-left transition hover:border-teal-400 hover:bg-teal-50/50 disabled:opacity-60"
              >
                <span className="text-2xl" aria-hidden>
                  {d.icon}
                </span>
                <span className="min-w-0">
                  <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                    {d.name} {busy === d.email && <Spinner className="h-3 w-3" />}
                  </span>
                  <span className="block text-[11px] font-medium uppercase tracking-wide text-teal-700">{d.role}</span>
                  <span className="block truncate text-xs text-slate-500">{d.note}</span>
                </span>
              </button>
            ))}
          </div>

          <div className="my-5 flex items-center gap-3 text-xs text-slate-400">
            <div className="h-px flex-1 bg-slate-200" /> or <div className="h-px flex-1 bg-slate-200" />
          </div>

          <form onSubmit={submit} className="space-y-3">
            <Field label="Email">
              <input className={inputCls} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
            </Field>
            <Field label="Password">
              <input className={inputCls} type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </Field>
            <ErrorBox error={error} />
            <button className={`${btn.primary} w-full`} disabled={!!busy}>
              {busy === email.trim() && <Spinner className="h-4 w-4 text-white" />} Sign in
            </button>
          </form>
          <p className="mt-4 text-center text-sm text-slate-500">
            New here?{' '}
            <Link to="/register" className="font-medium text-teal-700 hover:underline">
              Create an account
            </Link>
          </p>
          <p className="mt-2 text-center text-[11px] text-slate-400">Demo password for all accounts: demo1234</p>
        </div>
      </div>
    </AuthShell>
  )
}
