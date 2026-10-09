import type { ReactNode } from 'react'
import type { Overview, PatientProfile, TimelineItem, Vital } from '../types'
import { api } from '../api'
import { BpChart, LabTrendChart, normaliseBp } from './Charts'
import { SourceLink, useDocPreview } from './DocumentPreview'
import { Card, Empty, Pill, ageFrom, arr, fmtDate, titleCase } from './ui'

export const KEY_LABS = ['HbA1c', 'FBS', 'LDL', 'Creatinine']

function bmiBand(bmi?: number | null) {
  if (!bmi) return null
  if (bmi < 18.5) return { label: 'Underweight', cls: 'text-sky-600' }
  if (bmi < 23) return { label: 'Normal (Asian cut-off)', cls: 'text-emerald-600' }
  if (bmi < 25) return { label: 'Overweight', cls: 'text-amber-600' }
  return { label: 'Obese', cls: 'text-rose-600' }
}

export function computeBmi(p?: PatientProfile | null): number | null {
  if (!p) return null
  if (typeof p.bmi === 'number') return p.bmi
  if (p.height_cm && p.weight_kg) return Math.round((p.weight_kg / (p.height_cm / 100) ** 2) * 10) / 10
  return null
}

export function ProfileCard({ profile, name }: { profile?: PatientProfile | null; name?: string }) {
  const p = profile || ({} as PatientProfile)
  const age = ageFrom(p.dob)
  const bmi = computeBmi(p)
  const band = bmiBand(bmi)
  const allergies = arr(p.allergies)
  const hered = arr(p.hereditary_history)
  return (
    <Card bodyClass="p-0">
      <div className="flex flex-wrap items-center gap-4 border-b border-slate-100 bg-gradient-to-r from-teal-600 to-teal-500 px-4 py-4 text-white rounded-t-xl">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white/20 text-lg font-semibold">
          {(p.full_name || name || '?')
            .split(' ')
            .map((x) => x[0])
            .slice(0, 2)
            .join('')}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-lg font-semibold">{p.full_name || name || 'Patient'}</div>
          <div className="text-sm text-teal-50">
            {[age !== null ? `${age} y` : null, p.sex, p.occupation].filter(Boolean).join(' · ') || '—'}
          </div>
        </div>
        {p.dhid && (
          <div className="rounded-lg bg-white/15 px-3 py-1.5 text-right">
            <div className="text-[10px] uppercase tracking-wider text-teal-50">Digital Health ID</div>
            <div className="font-mono text-sm font-semibold tracking-wide">{p.dhid}</div>
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-px bg-slate-100 sm:grid-cols-4">
        <Mini label="Blood group" value={p.blood_group || '—'} />
        <Mini label="BMI" value={bmi ?? '—'} sub={band && <span className={band.cls}>{band.label}</span>} />
        <Mini label="Height" value={p.height_cm ? `${p.height_cm} cm` : '—'} />
        <Mini label="Weight" value={p.weight_kg ? `${p.weight_kg} kg` : '—'} />
      </div>
      <div className="space-y-3 p-4">
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Allergies</div>
          {allergies.length ? (
            <div className="flex flex-wrap gap-1.5">
              {allergies.map((a) => (
                <span key={a} className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700">
                  ⚠ {a}
                </span>
              ))}
            </div>
          ) : (
            <div className="text-sm text-slate-400">No known allergies</div>
          )}
        </div>
        {hered.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Family history</div>
            <div className="flex flex-wrap gap-1.5">
              {hered.map((h, i) => (
                <span key={i} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                  {titleCase(h.relation)}: {h.condition}
                </span>
              ))}
            </div>
          </div>
        )}
        {p.insurance && (p.insurance.provider || p.insurance.policy_number) && (
          <div className="text-xs text-slate-500">
            <span className="font-semibold uppercase tracking-wide">Insurance: </span>
            {[p.insurance.provider, p.insurance.policy_number, p.insurance.coverage ? `cover ${p.insurance.coverage}` : null].filter(Boolean).join(' · ')}
          </div>
        )}
      </div>
    </Card>
  )
}

function Mini({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="bg-white px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-900">{value}</div>
      {sub && <div className="text-[11px]">{sub}</div>}
    </div>
  )
}

export function ConditionsCard({ overview }: { overview: Overview }) {
  const xs = arr(overview.active_conditions)
  return (
    <Card title={`Active conditions (${xs.length})`}>
      {xs.length === 0 ? (
        <Empty>No active conditions recorded</Empty>
      ) : (
        <ul className="divide-y divide-slate-100">
          {xs.map((c, i) => (
            <li key={c.id ?? i} className="flex items-center justify-between gap-2 py-2">
              <div>
                <div className="text-sm font-medium text-slate-800">{c.name}</div>
                <div className="text-xs text-slate-500">
                  since {fmtDate(c.diagnosed_date)}
                  {c.severity ? ` · ${c.severity}` : ''}
                </div>
              </div>
              <SourceLink docId={c.source_document_id} page={c.source_page} filename={c.source_filename} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export function MedicationsCard({ overview }: { overview: Overview }) {
  const xs = arr(overview.active_medications)
  return (
    <Card title={`Current medications (${xs.length})`}>
      {xs.length === 0 ? (
        <Empty>No active medications</Empty>
      ) : (
        <ul className="divide-y divide-slate-100">
          {xs.map((m, i) => (
            <li key={m.id ?? i} className="flex items-center justify-between gap-2 py-2">
              <div className="min-w-0">
                <div className="text-sm font-medium text-slate-800">
                  💊 {m.drug_name} {m.dosage && <span className="font-normal text-slate-500">{m.dosage}</span>}
                </div>
                <div className="truncate text-xs text-slate-500">
                  {[m.frequency, m.route, m.instructions, m.start_date ? `since ${fmtDate(m.start_date)}` : null].filter(Boolean).join(' · ') || '—'}
                </div>
              </div>
              <SourceLink docId={m.source_document_id} page={m.source_page} filename={m.source_filename} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export function VitalsStrip({ vitals }: { vitals?: Vital[] }) {
  const xs = arr(vitals)
  if (xs.length === 0) return null
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
      {xs.map((v, i) => (
        <div key={v.id ?? i} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{v.kind}</div>
          <div className="text-base font-semibold text-slate-900">
            {v.value ?? v.value_num ?? '—'} <span className="text-xs font-normal text-slate-500">{v.unit}</span>
          </div>
          <div className="text-[10px] text-slate-400">{fmtDate(v.date)}</div>
        </div>
      ))}
    </div>
  )
}

export function TrendsGrid({ overview, cols = 'md:grid-cols-2' }: { overview: Overview; cols?: string }) {
  const trends = overview.lab_trends || {}
  const names = [...KEY_LABS, ...Object.keys(trends).filter((k) => !KEY_LABS.includes(k))]
  const bp = normaliseBp(overview.bp_series)
  return (
    <div className={`grid gap-4 ${cols}`}>
      {names
        .filter((n) => KEY_LABS.includes(n) || arr(trends[n]).length > 0)
        .map((n) => (
          <Card key={n}>
            <LabTrendChart name={n} points={arr(trends[n])} />
          </Card>
        ))}
      <Card>
        <BpChart series={bp} />
      </Card>
    </div>
  )
}

const KIND_ICON: Record<string, string> = {
  opd: '🩺',
  ipd: '🏥',
  diagnostic: '🧪',
  pharmacy: '💊',
  prescription: '📝',
  lab_report: '🧪',
  discharge_summary: '🏥',
  opd_note: '🩺',
  pharmacy_bill: '🧾',
  imaging_report: '🩻',
  insurance: '🛡',
}

export function TimelineList({ items, compact = false }: { items: TimelineItem[]; compact?: boolean }) {
  const open = useDocPreview()
  if (items.length === 0) return <Empty>Nothing on the timeline yet</Empty>
  return (
    <ol className="relative ml-3 border-l-2 border-slate-200">
      {items.map((t, i) => {
        const docId = t.kind === 'document' ? t.id : t.source_document_id
        const ipd = t.type === 'ipd'
        return (
          <li key={`${t.kind}-${t.id ?? i}`} className={compact ? 'mb-3 ml-5' : 'mb-5 ml-6'}>
            <span
              className={`absolute -left-[13px] flex h-6 w-6 items-center justify-center rounded-full border-2 border-white text-xs shadow ${
                ipd ? 'bg-rose-100' : t.kind === 'document' ? 'bg-slate-100' : 'bg-teal-100'
              }`}
            >
              {KIND_ICON[t.type || ''] || (t.kind === 'document' ? '📄' : '•')}
            </span>
            <div className={`rounded-lg border bg-white ${compact ? 'px-3 py-2' : 'px-4 py-3'} ${ipd ? 'border-rose-200' : 'border-slate-200'}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-slate-500">{fmtDate(t.date)}</span>
                  {t.type && <Pill value={ipd ? 'high' : t.kind === 'document' ? 'queued' : 'active'} label={titleCase(t.type)} />}
                </div>
                {docId ? (
                  <button onClick={() => open({ id: docId, filename: t.kind === 'document' ? t.title : undefined })} className="text-xs font-medium text-teal-700 hover:underline">
                    📎 View source
                  </button>
                ) : null}
              </div>
              <div className={`mt-1 font-medium text-slate-800 ${compact ? 'text-sm' : ''}`}>{t.title || '—'}</div>
              {t.subtitle && <div className="text-xs text-slate-500">{t.subtitle}</div>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/**
 * Normalise the overview payload. The backend may return `patient` instead of `profile`,
 * `latest_vitals` keyed by kind instead of an array, and `counts.lab_results` instead of `counts.labs`.
 */
export function normaliseOverview(raw: unknown): Overview {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const lv = r.latest_vitals
  let latest: Vital[] = []
  if (Array.isArray(lv)) latest = lv as Vital[]
  else if (lv && typeof lv === 'object')
    latest = Object.entries(lv as Record<string, Partial<Vital>>).map(([kind, v]) => ({ ...(v || {}), kind: v?.kind || kind }) as Vital)
  const counts = (r.counts || {}) as Record<string, number | undefined>
  return {
    ...(r as Overview),
    profile: (r.profile || r.patient || undefined) as PatientProfile | undefined,
    latest_vitals: latest,
    counts: { ...counts, labs: counts.labs ?? counts.lab_results, documents: counts.documents, encounters: counts.encounters },
  }
}

export const fetchOverview = (pid: number) => api.get<unknown>(`/patients/${pid}/overview`).then(normaliseOverview)
