import { useEffect, useState } from 'react'
import { api } from '../../api'
import { computeBmi } from '../../components/PatientSummary'
import { Card, ErrorBox, Field, Loading, PageHeader, Spinner, arr, btn, inputCls, useAsync } from '../../components/ui'
import type { FamilyMember, HereditaryItem, PatientProfile } from '../../types'

function PairList<T extends Record<string, string>>({
  rows,
  onChange,
  keys,
  labels,
  blank,
}: {
  rows: T[]
  onChange: (r: T[]) => void
  keys: (keyof T & string)[]
  labels: string[]
  blank: T
}) {
  return (
    <div className="space-y-2">
      {rows.map((r, i) => (
        <div key={i} className="flex gap-2">
          {keys.map((k, j) => (
            <input
              key={k}
              className={inputCls}
              placeholder={labels[j]}
              value={r[k] || ''}
              onChange={(e) => onChange(rows.map((x, xi) => (xi === i ? { ...x, [k]: e.target.value } : x)))}
            />
          ))}
          <button type="button" className="rounded-md px-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600" onClick={() => onChange(rows.filter((_, xi) => xi !== i))}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" className={btn.ghost} onClick={() => onChange([...rows, { ...blank }])}>
        + Add
      </button>
    </div>
  )
}

export default function Profile() {
  const { data, error, loading, reload } = useAsync(() => api.get<PatientProfile>('/patients/me/profile'), [])
  const [f, setF] = useState<PatientProfile | null>(null)
  const [allergyText, setAllergyText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (data) {
      setF({ ...data, insurance: data.insurance || {}, hereditary_history: arr(data.hereditary_history), family_members: arr(data.family_members) })
      setAllergyText(arr(data.allergies).join(', '))
    }
  }, [data])

  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!f) return null

  const set = <K extends keyof PatientProfile>(k: K, v: PatientProfile[K]) => {
    setSaved(false)
    setF({ ...f, [k]: v })
  }
  const num = (s: string) => (s === '' ? null : Number(s))
  const txt = (k: keyof PatientProfile) => (e: { target: { value: string } }) => set(k, (e.target.value || null) as never)

  const save = async () => {
    setSaving(true)
    setSaveErr(null)
    try {
      const payload: Partial<PatientProfile> = {
        dob: f.dob || null,
        sex: f.sex || null,
        phone: f.phone || null,
        address: f.address || null,
        occupation: f.occupation || null,
        blood_group: f.blood_group || null,
        height_cm: f.height_cm ?? null,
        weight_kg: f.weight_kg ?? null,
        emergency_contact: f.emergency_contact || null,
        allergies: allergyText
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        hereditary_history: arr(f.hereditary_history).filter((h) => h.relation || h.condition),
        family_members: arr(f.family_members).filter((m) => m.name || m.relation),
        insurance: f.insurance || null,
      }
      const r = await api.put<PatientProfile>('/patients/me/profile', payload)
      if (r && typeof r === 'object') setF({ ...f, ...r, insurance: r.insurance || {} })
      setSaved(true)
    } catch (e) {
      setSaveErr(e)
    } finally {
      setSaving(false)
    }
  }

  const bmi = computeBmi({ ...f, bmi: null })

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Profile"
        subtitle={
          <span>
            {f.full_name} · DHID <span className="font-mono font-semibold">{f.dhid || '—'}</span>
          </span>
        }
        action={
          <div className="flex items-center gap-3">
            {saved && <span className="text-sm text-emerald-600">✓ Saved</span>}
            <button className={btn.primary} onClick={save} disabled={saving}>
              {saving && <Spinner className="h-4 w-4 text-white" />} Save changes
            </button>
          </div>
        }
      />
      <ErrorBox error={saveErr} />
      <div className="mt-3 space-y-5">
        <Card title="Personal">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Date of birth">
              <input className={inputCls} type="date" value={f.dob || ''} onChange={txt('dob')} />
            </Field>
            <Field label="Sex">
              <select className={inputCls} value={f.sex || ''} onChange={txt('sex')}>
                <option value="">—</option>
                <option value="F">Female</option>
                <option value="M">Male</option>
                <option value="O">Other</option>
              </select>
            </Field>
            <Field label="Phone">
              <input className={inputCls} value={f.phone || ''} onChange={txt('phone')} />
            </Field>
            <Field label="Occupation">
              <input className={inputCls} value={f.occupation || ''} onChange={txt('occupation')} />
            </Field>
            <Field label="Address" className="sm:col-span-2">
              <input className={inputCls} value={f.address || ''} onChange={txt('address')} />
            </Field>
            <Field label="Emergency contact" className="sm:col-span-3">
              <input className={inputCls} value={f.emergency_contact || ''} onChange={txt('emergency_contact')} placeholder="Name, relation, phone" />
            </Field>
          </div>
        </Card>
        <Card title="Clinical basics">
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Blood group">
              <select className={inputCls} value={f.blood_group || ''} onChange={txt('blood_group')}>
                <option value="">—</option>
                {['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'].map((b) => (
                  <option key={b}>{b}</option>
                ))}
              </select>
            </Field>
            <Field label="Height (cm)">
              <input className={inputCls} type="number" value={f.height_cm ?? ''} onChange={(e) => set('height_cm', num(e.target.value))} />
            </Field>
            <Field label="Weight (kg)">
              <input className={inputCls} type="number" value={f.weight_kg ?? ''} onChange={(e) => set('weight_kg', num(e.target.value))} />
            </Field>
            <Field label="BMI (computed)">
              <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">{bmi ?? '—'}</div>
            </Field>
            <Field label="Allergies (comma-separated)" className="sm:col-span-4">
              <input className={inputCls} value={allergyText} onChange={(e) => (setSaved(false), setAllergyText(e.target.value))} placeholder="e.g. Sulfa drugs, Penicillin" />
            </Field>
          </div>
        </Card>
        <div className="grid gap-5 md:grid-cols-2">
          <Card title="Hereditary / family history">
            <PairList<HereditaryItem & Record<string, string>>
              rows={arr(f.hereditary_history) as (HereditaryItem & Record<string, string>)[]}
              onChange={(r) => set('hereditary_history', r)}
              keys={['relation', 'condition']}
              labels={['Relation', 'Condition']}
              blank={{ relation: '', condition: '' }}
            />
          </Card>
          <Card title="Family members">
            <PairList<FamilyMember & Record<string, string>>
              rows={arr(f.family_members) as (FamilyMember & Record<string, string>)[]}
              onChange={(r) => set('family_members', r)}
              keys={['name', 'relation']}
              labels={['Name', 'Relation']}
              blank={{ name: '', relation: '' }}
            />
          </Card>
        </div>
        <Card title="Insurance">
          <div className="grid gap-3 sm:grid-cols-3">
            {(['provider', 'policy_number', 'coverage'] as const).map((k) => (
              <Field key={k} label={k === 'policy_number' ? 'Policy no.' : k[0].toUpperCase() + k.slice(1)}>
                <input
                  className={inputCls}
                  value={f.insurance?.[k] == null ? '' : String(f.insurance[k])}
                  onChange={(e) => set('insurance', { ...(f.insurance || {}), [k]: e.target.value || null })}
                />
              </Field>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
