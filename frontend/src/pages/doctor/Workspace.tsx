import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CONSENT_MESSAGE, api, isConsentError } from '../../api'
import AskPanel from '../../components/AskPanel'
import { BpChart, LabTrendChart, normaliseBp } from '../../components/Charts'
import NoteForm from '../../components/NoteForm'
import { ConditionsCard, KEY_LABS, MedicationsCard, TimelineList, VitalsStrip, computeBmi, fetchOverview } from '../../components/PatientSummary'
import { Card, ErrorBox, Loading, Pill, Tabs, ageFrom, arr, fmtDate, msUntil, useAsync, useNow } from '../../components/ui'
import type { DoctorPatient, Overview, TimelineItem } from '../../types'
import { RecordsPanel } from '../patient/Records'
import { timeLeft } from './Patients'

type Tab = 'summary' | 'records' | 'timeline' | 'note'

function Locked() {
  return (
    <div className="mx-auto mt-10 max-w-md rounded-2xl border border-amber-300 bg-amber-50 p-8 text-center shadow-sm">
      <div className="text-5xl">🔒</div>
      <h2 className="mt-3 text-lg font-semibold text-amber-900">{CONSENT_MESSAGE}</h2>
      <p className="mt-2 text-sm text-amber-800">
        Your time-bound access to this patient has ended or was revoked. No data from this record is shown once consent lapses.
      </p>
      <Link to="/doctor" className="mt-5 inline-flex rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700">
        ← Back to my patients
      </Link>
    </div>
  )
}

export default function Workspace() {
  const { id } = useParams()
  const pid = Number(id)
  const now = useNow(30000)
  const [tab, setTab] = useState<Tab>('summary')
  const [locked, setLocked] = useState(false)
  const lockTimer = useRef<number | null>(null)
  const overview = useAsync(() => fetchOverview(pid), [pid])
  const timeline = useAsync(() => api.get<TimelineItem[]>(`/patients/${pid}/timeline`), [pid])
  const grants = useAsync(() => api.get<DoctorPatient[]>('/doctor/patients'), [pid])

  useEffect(() => {
    if (isConsentError(overview.error) || isConsentError(timeline.error)) setLocked(true)
  }, [overview.error, timeline.error])
  useEffect(() => {
    setLocked(false)
    // A pending consent-lost lock belongs to the previous patient.
    if (lockTimer.current !== null) window.clearTimeout(lockTimer.current)
    lockTimer.current = null
  }, [pid])

  const meta = arr(grants.data).find((g) => g.patient_id === pid)
  const left = timeLeft(meta?.expires_at, now)
  // Grant expires while the page is open: a one-off timer locks exactly at expires_at
  // (the 30 s `now` tick only drives the countdown label), so stale data is never shown.
  const expiresAt = meta?.expires_at
  useEffect(() => {
    const ms = msUntil(expiresAt, Date.now())
    if (ms === null) return
    if (ms <= 0) {
      setLocked(true)
      return
    }
    // setTimeout caps at ~24.8 days; grants are ≤ 7 days.
    const t = window.setTimeout(() => setLocked(true), Math.min(ms, 2_147_000_000))
    return () => window.clearTimeout(t)
  }, [expiresAt])

  // Consent lost during an Ask: let the doctor read the message briefly, then lock. Cleared on unmount.
  useEffect(
    () => () => {
      if (lockTimer.current !== null) window.clearTimeout(lockTimer.current)
    },
    [],
  )
  const onConsentLost = useCallback(() => {
    if (lockTimer.current !== null) window.clearTimeout(lockTimer.current)
    lockTimer.current = window.setTimeout(() => setLocked(true), 2500)
  }, [])

  if (locked) return <Locked />
  if (overview.loading && !overview.data) return <Loading label="Opening patient record…" />
  if (overview.error) return <ErrorBox error={overview.error} onRetry={overview.reload} />
  const o = overview.data || {}
  const p = o.profile || ({} as NonNullable<Overview['profile']>)
  const name = p.full_name || meta?.full_name || `Patient #${pid}`
  const age = ageFrom(p.dob) ?? meta?.age
  const bmi = computeBmi(p)
  const canWrite = meta?.scope === 'read_write'
  const trends = o.lab_trends || {}
  const bp = normaliseBp(o.bp_series)
  const allergies = arr(p.allergies)
  const tl = arr(timeline.data)

  const tabs: { id: Tab; label: string }[] = [
    { id: 'summary', label: 'At a glance' },
    { id: 'records', label: 'Records' },
    { id: 'timeline', label: `Timeline (${tl.length})` },
    ...(canWrite ? [{ id: 'note' as Tab, label: '✎ Consultation note' }] : []),
  ]

  return (
    <div className="-m-3 sm:-m-6">
      {/* Patient banner */}
      <div className="border-b border-slate-200 bg-white px-3 py-3 sm:px-6">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <Link to="/doctor" className="text-sm text-slate-400 hover:text-slate-700">
            ← Patients
          </Link>
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-teal-600 text-sm font-semibold text-white">
              {name
                .split(' ')
                .map((x) => x[0])
                .slice(0, 2)
                .join('')}
            </div>
            <div>
              <div className="text-lg font-semibold leading-tight text-slate-900">{name}</div>
              <div className="text-xs text-slate-500">
                {[age != null ? `${age} y` : null, p.sex || meta?.sex, p.blood_group, bmi ? `BMI ${bmi}` : null].filter(Boolean).join(' · ')}
                {(p.dhid || meta?.dhid) && <span className="ml-2 font-mono">{p.dhid || meta?.dhid}</span>}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {allergies.length > 0 ? (
              allergies.map((a) => (
                <span key={a} className="rounded-full border border-rose-300 bg-rose-50 px-2 py-0.5 text-xs font-semibold text-rose-700">
                  ⚠ Allergy: {a}
                </span>
              ))
            ) : (
              <span className="text-xs text-slate-400">NKDA</span>
            )}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Pill value={canWrite ? 'doctor' : 'active'} label={canWrite ? 'View & notes' : 'View only'} />
            {left && (
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${left.endsWith('m left') ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`} title={meta?.expires_at ? `Access until ${fmtDate(meta.expires_at, true)}` : ''}>
                ⏱ Access {left}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-3 sm:p-6 xl:grid-cols-12">
        {/* Left: record */}
        <div className="min-w-0 xl:col-span-7">
          <Tabs<Tab> tabs={tabs} value={tab} onChange={setTab} />
          {tab === 'summary' && (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3">
                {[
                  ['Documents', o.counts?.documents],
                  ['Encounters', o.counts?.encounters],
                  ['Lab results', o.counts?.labs],
                ].map(([l, v]) => (
                  <div key={l as string} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{l}</div>
                    <div className="text-xl font-semibold text-slate-900">{(v as number) ?? 0}</div>
                  </div>
                ))}
              </div>
              <VitalsStrip vitals={o.latest_vitals} />
              <div className="grid gap-4 md:grid-cols-2">
                <ConditionsCard overview={o} />
                <MedicationsCard overview={o} />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <Card>
                  <LabTrendChart name="HbA1c" points={arr(trends.HbA1c)} height={200} />
                </Card>
                <Card>
                  <BpChart series={bp} height={200} />
                </Card>
              </div>
              <div className="grid gap-4 md:grid-cols-3">
                {KEY_LABS.filter((k) => k !== 'HbA1c').map((k) => (
                  <Card key={k}>
                    <LabTrendChart name={k} points={arr(trends[k])} height={130} />
                  </Card>
                ))}
              </div>
              <Card
                title="Recent timeline"
                action={
                  tl.length > 6 && (
                    <button className="text-xs font-medium text-teal-700 hover:underline" onClick={() => setTab('timeline')}>
                      See all {tl.length} →
                    </button>
                  )
                }
              >
                {timeline.loading && !timeline.data ? <Loading /> : timeline.error ? <ErrorBox error={timeline.error} /> : <TimelineList items={tl.slice(0, 6)} compact />}
              </Card>
            </div>
          )}
          {tab === 'records' && <RecordsPanel patientId={pid} />}
          {tab === 'timeline' && (timeline.error ? <ErrorBox error={timeline.error} /> : <TimelineList items={tl} />)}
          {tab === 'note' && canWrite && (
            <Card title="New consultation note">
              <NoteForm
                patientId={pid}
                onSaved={() => {
                  overview.reload()
                  timeline.reload()
                }}
              />
            </Card>
          )}
        </div>

        {/* Right: Ask */}
        <div className="xl:col-span-5">
          <div className="xl:sticky xl:top-20 xl:h-[calc(100vh-7rem)]">
            <AskPanel key={pid} patientId={pid} patientName={name} sex={p.sex || meta?.sex} onConsentLost={onConsentLost} />
          </div>
        </div>
      </div>
    </div>
  )
}
