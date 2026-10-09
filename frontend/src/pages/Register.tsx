import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { homeFor, useAuth } from '../auth'
import { ErrorBox, Field, Spinner, btn, inputCls } from '../components/ui'
import { AuthShell } from './Login'

export default function Register() {
  const { user, register } = useAuth()
  const nav = useNavigate()
  const [role, setRole] = useState<'patient' | 'doctor'>('patient')
  const [f, setF] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  if (user) return <Navigate to={homeFor(user.role)} replace />
  const set = (k: string) => (e: { target: { value: string } }) => setF((p) => ({ ...p, [k]: e.target.value }))

  const submit = async (ev: FormEvent) => {
    ev.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const payload: Record<string, unknown> = {
        email: (f.email || '').trim(),
        password: f.password || '',
        full_name: (f.full_name || '').trim(),
        role,
      }
      const extra = role === 'doctor' ? ['license_no', 'specialization', 'qualification', 'facility'] : ['dob', 'sex', 'phone']
      extra.forEach((k) => {
        if (f[k]) payload[k] = f[k]
      })
      const u = await register(payload)
      nav(homeFor(u.role), { replace: true })
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell>
      <form onSubmit={submit} className="w-full max-w-lg space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-xl sm:p-8">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Create your account</h2>
          <p className="mt-1 text-sm text-slate-500">Mock registration — no verification. Use synthetic details only.</p>
        </div>
        <div className="grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1">
          {(['patient', 'doctor'] as const).map((r) => (
            <button
              type="button"
              key={r}
              onClick={() => setRole(r)}
              className={`rounded-lg py-2 text-sm font-medium capitalize transition ${role === r ? 'bg-white text-teal-700 shadow' : 'text-slate-500'}`}
            >
              {r === 'patient' ? '👤 Patient' : '🩺 Doctor'}
            </button>
          ))}
        </div>
        <Field label="Full name">
          <input className={inputCls} required value={f.full_name || ''} onChange={set('full_name')} />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Email">
            <input className={inputCls} type="email" required value={f.email || ''} onChange={set('email')} />
          </Field>
          <Field label="Password">
            <input className={inputCls} type="password" required minLength={6} value={f.password || ''} onChange={set('password')} />
          </Field>
        </div>
        {role === 'doctor' ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="License no.">
              <input className={inputCls} value={f.license_no || ''} onChange={set('license_no')} placeholder="e.g. MCI-12345" />
            </Field>
            <Field label="Specialization">
              <input className={inputCls} value={f.specialization || ''} onChange={set('specialization')} placeholder="e.g. Endocrinology" />
            </Field>
            <Field label="Qualification">
              <input className={inputCls} value={f.qualification || ''} onChange={set('qualification')} placeholder="MBBS, MD" />
            </Field>
            <Field label="Facility">
              <input className={inputCls} value={f.facility || ''} onChange={set('facility')} />
            </Field>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Date of birth">
              <input className={inputCls} type="date" value={f.dob || ''} onChange={set('dob')} />
            </Field>
            <Field label="Sex">
              <select className={inputCls} value={f.sex || ''} onChange={set('sex')}>
                <option value="">—</option>
                <option value="F">Female</option>
                <option value="M">Male</option>
                <option value="O">Other</option>
              </select>
            </Field>
            <Field label="Phone">
              <input className={inputCls} value={f.phone || ''} onChange={set('phone')} />
            </Field>
          </div>
        )}
        <ErrorBox error={error} />
        <button className={`${btn.primary} w-full`} disabled={busy}>
          {busy && <Spinner className="h-4 w-4 text-white" />} Create account
        </button>
        <p className="text-center text-sm text-slate-500">
          Already registered?{' '}
          <Link to="/login" className="font-medium text-teal-700 hover:underline">
            Sign in
          </Link>
        </p>
      </form>
    </AuthShell>
  )
}
