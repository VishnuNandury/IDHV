import { QRCodeSVG } from 'qrcode.react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Card, Empty, ErrorBox, Loading, PageHeader, Pill, Spinner, arr, btn, fmtDate, msUntil, useAsync, useNow } from '../../components/ui'
import type { Grant, GrantCreated, PatientProfile } from '../../types'

const DURATIONS = [
  { v: 60, label: '1 hour' },
  { v: 1440, label: '24 hours' },
  { v: 10080, label: '7 days' },
]

function remaining(target: string | null | undefined, now: number) {
  if (!target) return null
  return msUntil(target, now)
}
function fmtCountdown(ms: number) {
  if (ms <= 0) return '00:00'
  const s = Math.floor(ms / 1000)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

export default function Access() {
  const [minutes, setMinutes] = useState(1440)
  const [scope, setScope] = useState<'read' | 'read_write'>('read')
  const [creating, setCreating] = useState(false)
  // The created grant carries the duration/scope it was issued with, so later changes to the
  // pickers don't relabel a code that's already on screen.
  const [created, setCreated] = useState<(GrantCreated & { minutes: number; scope: string }) | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const now = useNow(1000)
  const grants = useAsync(() => api.get<Grant[]>('/consent/grants'), [])
  const profile = useAsync(() => api.get<PatientProfile>('/patients/me/profile'), [])

  const create = async () => {
    setCreating(true)
    setErr(null)
    try {
      const g = await api.post<GrantCreated>('/consent/grants', { access_minutes: minutes, scope })
      setCreated({ ...g, minutes: g.access_minutes ?? minutes, scope: g.scope ?? scope })
      grants.reload()
    } catch (e) {
      setErr(e)
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id: number) => {
    if (!window.confirm('Revoke this access? The doctor will lose access immediately.')) return
    try {
      await api.del(`/consent/grants/${id}`)
      if (created?.grant_id === id) setCreated(null)
      grants.reload()
    } catch (e) {
      setErr(e)
    }
  }

  const otpLeft = created ? remaining(created.otp_expires_at, now) : null
  const otpExpired = otpLeft !== null && otpLeft <= 0
  const createdGrant = created ? arr(grants.data).find((g) => g.id === created.grant_id) : undefined
  const redeemed = createdGrant && createdGrant.status !== 'pending'
  const qrValue = created ? (typeof created.qr_payload === 'string' ? created.qr_payload : JSON.stringify(created.qr_payload)) : ''
  const dhid = profile.data?.dhid || created?.dhid

  // While a fresh code is on screen, poll so the patient sees the moment it is redeemed.
  const waiting = !!created && !redeemed && !otpExpired
  const reloadGrants = grants.reload
  useEffect(() => {
    if (!waiting) return
    const t = window.setInterval(reloadGrants, 4000)
    return () => window.clearInterval(t)
  }, [waiting, reloadGrants])

  return (
    <div>
      <PageHeader
        title="Access control"
        subtitle="You decide who sees your records, for how long. Share a one-time code with your doctor; revoke anytime."
        action={
          dhid && (
            <div className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-right">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Your DHID</div>
              <div className="font-mono text-sm font-semibold text-slate-800">{dhid}</div>
            </div>
          )
        }
      />
      <ErrorBox error={err} />
      <div className="mt-3 grid gap-5 lg:grid-cols-5">
        <Card title="Grant a doctor access" className="lg:col-span-2">
          <div className="space-y-4">
            <div>
              <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Access duration</div>
              <div className="grid grid-cols-3 gap-2">
                {DURATIONS.map((d) => (
                  <button
                    key={d.v}
                    onClick={() => setMinutes(d.v)}
                    className={`rounded-lg border px-3 py-2 text-sm font-medium ${minutes === d.v ? 'border-teal-500 bg-teal-50 text-teal-800' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Permission</div>
              <div className="space-y-2">
                {[
                  { v: 'read' as const, t: 'View only', d: 'Doctor can view records and ask questions' },
                  { v: 'read_write' as const, t: 'View & add notes', d: 'Doctor can also write consultation notes & prescriptions' },
                ].map((o) => (
                  <label key={o.v} className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${scope === o.v ? 'border-teal-500 bg-teal-50/60' : 'border-slate-200'}`}>
                    <input type="radio" className="mt-1 accent-teal-600" checked={scope === o.v} onChange={() => setScope(o.v)} />
                    <span>
                      <span className="block text-sm font-medium text-slate-800">{o.t}</span>
                      <span className="block text-xs text-slate-500">{o.d}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <button className={`${btn.primary} w-full`} onClick={create} disabled={creating}>
              {creating && <Spinner className="h-4 w-4 text-white" />} Generate one-time code
            </button>
            <p className="text-xs text-slate-500">The code is valid for 10 minutes. Access starts when the doctor redeems it.</p>
          </div>
        </Card>

        <Card title="Share with your doctor" className="lg:col-span-3">
          {!created ? (
            <div className="flex h-full min-h-[260px] flex-col items-center justify-center gap-2 text-center text-sm text-slate-400">
              <div className="text-5xl">🔐</div>
              Generate a code to show it here as digits and a QR code.
            </div>
          ) : (
            <div className="flex flex-col items-center gap-6 md:flex-row md:items-center md:justify-center">
              <div className={`rounded-xl border border-slate-200 bg-white p-3 ${otpExpired || redeemed ? 'opacity-30' : ''}`}>
                <QRCodeSVG value={qrValue} size={184} level="M" fgColor="#0f172a" />
              </div>
              <div className="text-center md:text-left">
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">One-time code</div>
                <div className={`otp-digit mt-1 flex gap-1.5 ${otpExpired || redeemed ? 'opacity-40' : ''}`}>
                  {String(created.otp)
                    .split('')
                    .map((c, i) => (
                      <span key={i} className="flex h-14 w-11 items-center justify-center rounded-lg border-2 border-teal-200 bg-teal-50 font-mono text-3xl font-bold text-teal-800">
                        {c}
                      </span>
                    ))}
                </div>
                {dhid && (
                  <div className="mt-3 text-sm text-slate-600">
                    with DHID <span className="font-mono font-semibold">{dhid}</span>
                  </div>
                )}
                <div className="mt-3">
                  {redeemed ? (
                    <Pill value="active" label={`✓ Redeemed${createdGrant && doctorOf(createdGrant) ? ` by ${doctorOf(createdGrant)!.full_name}` : ''}`} />
                  ) : otpExpired ? (
                    <Pill value="expired" label="Code expired — generate a new one" />
                  ) : (
                    <div className="text-sm text-slate-600">
                      Expires in <span className="font-mono text-lg font-semibold text-amber-600">{fmtCountdown(otpLeft ?? 0)}</span>
                    </div>
                  )}
                </div>
                <div className="mt-2 text-xs text-slate-500">
                  {DURATIONS.find((d) => d.v === created.minutes)?.label || `${created.minutes} min`} ·{' '}
                  {created.scope === 'read_write' ? 'view & add notes' : 'view only'}
                </div>
              </div>
            </div>
          )}
        </Card>
      </div>

      <Card title="Access grants" className="mt-5" bodyClass="p-0" action={<button className={btn.ghost} onClick={grants.reload}>↻ Refresh</button>}>
        {grants.loading && !grants.data && <Loading />}
        {grants.error ? (
          <div className="p-4">
            <ErrorBox error={grants.error} onRetry={grants.reload} />
          </div>
        ) : null}
        {grants.data && arr(grants.data).length === 0 && (
          <div className="p-4">
            <Empty>No grants yet</Empty>
          </div>
        )}
        {grants.data && arr(grants.data).length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {['Doctor', 'Status', 'Scope', 'Created', 'Access until', ''].map((h) => (
                    <th key={h} className="px-4 py-2 text-left font-semibold">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {arr(grants.data).map((g) => {
                  const left = g.status === 'active' ? remaining(g.expires_at, now) : null
                  return (
                    <tr key={g.id}>
                      <td className="px-4 py-2">
                        {doctorOf(g) ? (
                          <div>
                            <div className="font-medium text-slate-800">{doctorOf(g)!.full_name}</div>
                            <div className="text-xs text-slate-500">{doctorOf(g)!.specialization || ''}</div>
                          </div>
                        ) : (
                          <span className="text-slate-400">Awaiting redemption{g.otp ? ` · code ${g.otp}` : ''}</span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        <Pill value={g.status} />
                      </td>
                      <td className="px-4 py-2 text-slate-600">{g.scope === 'read_write' ? 'View & notes' : 'View only'}</td>
                      <td className="px-4 py-2 text-slate-600">{fmtDate(g.created_at, true)}</td>
                      <td className="px-4 py-2 text-slate-600">
                        {g.expires_at ? fmtDate(g.expires_at, true) : '—'}
                        {left !== null && left > 0 && <div className="text-xs text-teal-700">{fmtCountdown(left)} left</div>}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {(g.status === 'active' || g.status === 'pending') && (
                          <button className={btn.danger} onClick={() => revoke(g.id)}>
                            Revoke
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

/** Backend may return a nested `doctor` object or flat `doctor_name` / `doctor_specialization`. */
function doctorOf(g: Grant): { full_name: string; specialization?: string | null } | null {
  if (g.doctor && g.doctor.full_name) return g.doctor
  if (g.doctor_name) return { full_name: g.doctor_name, specialization: g.doctor_specialization }
  return null
}
