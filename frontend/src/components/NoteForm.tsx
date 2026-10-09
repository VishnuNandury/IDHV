import { useState, type FormEvent } from 'react'
import { api } from '../api'
import type { NotePayload } from '../types'
import { ErrorBox, Field, Spinner, btn, inputCls, localToday } from './ui'

type Med = NotePayload['medications'][number]
const blankMed = (): Med => ({ drug_name: '', dosage: '', frequency: '', duration: '', instructions: '' })
const today = localToday

export default function NoteForm({ patientId, onSaved }: { patientId: number; onSaved?: () => void }) {
  const [f, setF] = useState({ date: today(), reason: '', diagnosis: '', notes: '', advice: '', follow_up_date: '', fee: '' })
  const [meds, setMeds] = useState<Med[]>([blankMed()])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const [ok, setOk] = useState(false)
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    setOk(false)
    setF((p) => ({ ...p, [k]: e.target.value }))
  }
  const setMed = (i: number, k: keyof Med, v: string) => setMeds((ms) => ms.map((m, j) => (j === i ? { ...m, [k]: v } : m)))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      const fee = f.fee.trim() === '' ? null : Number(f.fee.replace(/[₹,\s]/g, ''))
      const payload: NotePayload = {
        date: f.date,
        reason: f.reason,
        diagnosis: f.diagnosis,
        notes: f.notes,
        advice: f.advice,
        follow_up_date: f.follow_up_date || null,
        fee: fee === null || isNaN(fee) ? null : fee,
        medications: meds.filter((m) => m.drug_name.trim()),
      }
      await api.post(`/doctor/patients/${patientId}/notes`, payload)
      setOk(true)
      setF({ date: today(), reason: '', diagnosis: '', notes: '', advice: '', follow_up_date: '', fee: '' })
      setMeds([blankMed()])
      onSaved?.()
    } catch (e2) {
      setErr(e2)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Visit date">
          <input type="date" className={inputCls} value={f.date} onChange={set('date')} required />
        </Field>
        <Field label="Follow-up">
          <input type="date" className={inputCls} value={f.follow_up_date} onChange={set('follow_up_date')} />
        </Field>
        <Field label="Fee (₹)">
          <input className={inputCls} inputMode="decimal" value={f.fee} onChange={set('fee')} placeholder="800" />
        </Field>
        <Field label="Chief complaint / reason" className="sm:col-span-3">
          <input className={inputCls} value={f.reason} onChange={set('reason')} required />
        </Field>
        <Field label="Diagnosis" className="sm:col-span-3">
          <input className={inputCls} value={f.diagnosis} onChange={set('diagnosis')} />
        </Field>
        <Field label="Clinical notes" className="sm:col-span-3">
          <textarea className={`${inputCls} min-h-[72px]`} value={f.notes} onChange={set('notes')} />
        </Field>
        <Field label="Advice (diet / exercise)" className="sm:col-span-3">
          <input className={inputCls} value={f.advice} onChange={set('advice')} />
        </Field>
      </div>
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">℞ Prescription</span>
          <button type="button" className={btn.ghost} onClick={() => setMeds((m) => [...m, blankMed()])}>
            + Add drug
          </button>
        </div>
        <div className="space-y-2">
          {meds.map((m, i) => (
            <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-slate-200 p-2 sm:grid-cols-[2fr_1fr_1fr_1fr_2fr_auto]">
              <input className={inputCls} placeholder="Drug (e.g. Tab Metformin)" value={m.drug_name} onChange={(e) => setMed(i, 'drug_name', e.target.value)} />
              <input className={inputCls} placeholder="500 mg" value={m.dosage} onChange={(e) => setMed(i, 'dosage', e.target.value)} />
              <input className={inputCls} placeholder="BD / 1-0-1" value={m.frequency} onChange={(e) => setMed(i, 'frequency', e.target.value)} />
              <input className={inputCls} placeholder="30 days" value={m.duration} onChange={(e) => setMed(i, 'duration', e.target.value)} />
              <input className={inputCls} placeholder="After food" value={m.instructions} onChange={(e) => setMed(i, 'instructions', e.target.value)} />
              <button type="button" className="rounded-md px-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600" onClick={() => setMeds((ms) => ms.filter((_, j) => j !== i))}>
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>
      <ErrorBox error={err} />
      {ok && <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">✓ Note saved to the patient's record</div>}
      <div className="flex justify-end">
        <button className={btn.primary} disabled={busy}>
          {busy && <Spinner className="h-4 w-4 text-white" />} Save consultation note
        </button>
      </div>
    </form>
  )
}
