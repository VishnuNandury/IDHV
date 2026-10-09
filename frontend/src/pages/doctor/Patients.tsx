import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorBox, Field, Loading, PageHeader, Pill, Spinner, arr, btn, fmtDate, inputCls, msUntil, useAsync, useNow } from '../../components/ui'
import type { DoctorPatient, RedeemResponse } from '../../types'

export function timeLeft(expires: string | null | undefined, now: number): string | null {
  if (!expires) return null
  const ms = msUntil(expires, now)
  if (ms === null) return null
  if (ms <= 0) return 'expired'
  const m = Math.floor(ms / 60000)
  if (m < 60) return `${m}m left`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h ${m % 60}m left`
  return `${Math.floor(h / 24)}d ${h % 24}h left`
}

function parsePayload(raw: string): { dhid?: string; otp?: string } | null {
  const s = raw.trim()
  if (!s) return null
  try {
    const j = JSON.parse(s)
    if (j && typeof j === 'object') return { dhid: j.dhid ? String(j.dhid) : undefined, otp: j.otp ? String(j.otp) : undefined }
  } catch {
    /* fall through to regex */
  }
  const dhid = s.match(/DH-\d{4}-\d{4}-\d{4}/i)?.[0]
  const otp = s.replace(dhid || '', '').match(/\b\d{6}\b/)?.[0]
  return dhid || otp ? { dhid: dhid?.toUpperCase(), otp } : null
}

export default function Patients() {
  const { user } = useAuth()
  const nav = useNavigate()
  const now = useNow(30000)
  const { data, error, loading, reload } = useAsync(() => api.get<DoctorPatient[]>('/doctor/patients'), [])
  const [mode, setMode] = useState<'otp' | 'qr'>('otp')
  const [dhid, setDhid] = useState('')
  const [otp, setOtp] = useState('')
  const [payload, setPayload] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)

  const redeem = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null)
    let body = { dhid: dhid.trim().toUpperCase(), otp: otp.trim() }
    if (mode === 'qr') {
      const p = parsePayload(payload)
      if (!p?.dhid || !p?.otp) {
        setErr(new Error('Could not read a DHID and 6-digit OTP from that payload'))
        return
      }
      body = { dhid: p.dhid, otp: p.otp }
    }
    setBusy(true)
    try {
      const r = await api.post<RedeemResponse>('/consent/redeem', body)
      nav(`/doctor/patients/${r.patient_id}`)
    } catch (e2) {
      setErr(e2)
    } finally {
      setBusy(false)
    }
  }

  const patients = arr(data)

  return (
    <div>
      <PageHeader title={`Good day, ${user?.full_name || 'Doctor'}`} subtitle="Patients who have granted you time-bound access. Access ends automatically — or when the patient revokes it." />
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card title={`My patients (${patients.length})`} action={<button className={btn.ghost} onClick={reload}>↻ Refresh</button>} bodyClass="p-0">
            {loading && !data && <Loading />}
            {error ? (
              <div className="p-4">
                <ErrorBox error={error} onRetry={reload} />
              </div>
            ) : null}
            {data && patients.length === 0 && (
              <div className="p-4">
                <Empty>No active grants. Ask a patient for their DHID and one-time code.</Empty>
              </div>
            )}
            <ul className="divide-y divide-slate-100">
              {patients.map((p) => {
                const left = timeLeft(p.expires_at, now)
                return (
                  <li key={p.patient_id}>
                    <Link to={`/doctor/patients/${p.patient_id}`} className="flex items-center gap-4 px-4 py-3 hover:bg-teal-50/40">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-teal-100 text-sm font-semibold text-teal-800">
                        {p.full_name
                          .split(' ')
                          .map((x) => x[0])
                          .slice(0, 2)
                          .join('')}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-slate-800">{p.full_name}</div>
                        <div className="text-xs text-slate-500">
                          {[p.age ? `${p.age} y` : null, p.sex, p.dhid].filter(Boolean).join(' · ')}
                        </div>
                      </div>
                      <div className="text-right">
                        <Pill value={p.scope === 'read_write' ? 'doctor' : 'active'} label={p.scope === 'read_write' ? 'View & notes' : 'View only'} />
                        <div className="mt-1 text-xs text-slate-500" title={fmtDate(p.expires_at, true)}>
                          ⏱ {left || '—'}
                        </div>
                      </div>
                      <span className="text-slate-300">›</span>
                    </Link>
                  </li>
                )
              })}
            </ul>
          </Card>
        </div>
        <Card title="➕ Add patient">
          <div className="mb-3 grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1 text-sm">
            {(
              [
                ['otp', 'DHID + OTP'],
                ['qr', 'Paste QR payload'],
              ] as const
            ).map(([m, l]) => (
              <button key={m} type="button" onClick={() => setMode(m)} className={`rounded-md py-1.5 font-medium ${mode === m ? 'bg-white text-teal-700 shadow' : 'text-slate-500'}`}>
                {l}
              </button>
            ))}
          </div>
          <form onSubmit={redeem} className="space-y-3">
            {mode === 'otp' ? (
              <>
                <Field label="Patient DHID">
                  <input className={`${inputCls} font-mono`} placeholder="DH-XXXX-XXXX-XXXX" value={dhid} onChange={(e) => setDhid(e.target.value)} required />
                </Field>
                <Field label="One-time code">
                  <input
                    className={`${inputCls} text-center font-mono text-2xl tracking-[0.5em]`}
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="••••••"
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    required
                  />
                </Field>
              </>
            ) : (
              <Field label="QR payload">
                <textarea className={`${inputCls} min-h-[96px] font-mono text-xs`} placeholder='{"dhid":"DH-8923-4102-9981","otp":"123456"}' value={payload} onChange={(e) => setPayload(e.target.value)} required />
              </Field>
            )}
            <ErrorBox error={err} />
            <button className={`${btn.primary} w-full`} disabled={busy}>
              {busy && <Spinner className="h-4 w-4 text-white" />} Redeem access
            </button>
            <p className="text-xs text-slate-500">The patient generates the code from their Access Control page. Codes expire after 10 minutes.</p>
          </form>
        </Card>
      </div>
    </div>
  )
}
