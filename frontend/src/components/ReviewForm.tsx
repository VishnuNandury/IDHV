import { useState } from 'react'
import type { ExEncounter, ExtractionResult } from '../types'
import { Field, arr, inputCls, titleCase } from './ui'

export const DOC_TYPES = ['prescription', 'lab_report', 'discharge_summary', 'opd_note', 'pharmacy_bill', 'imaging_report', 'insurance', 'other']
const ENC_TYPES = ['opd', 'ipd', 'diagnostic', 'pharmacy']
const FLAGS = ['', 'normal', 'high', 'low']
const VITAL_KINDS = ['bp', 'weight', 'glucose', 'pulse', 'spo2', 'temp']
const COND_STATUS = ['active', 'resolved']

type Row = Record<string, unknown>
interface Col {
  key: string
  label: string
  type?: 'text' | 'date' | 'select'
  options?: string[]
  w?: string
}

function EditableTable({
  title,
  rows,
  cols,
  onChange,
  blank,
}: {
  title: string
  rows: Row[]
  cols: Col[]
  onChange: (rows: Row[]) => void
  blank: Row
}) {
  const update = (i: number, k: string, v: string) => onChange(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {title} <span className="ml-1 rounded-full bg-slate-100 px-1.5 text-slate-600">{rows.length}</span>
        </h4>
        <button type="button" onClick={() => onChange([...rows, { ...blank }])} className="text-xs font-medium text-teal-700 hover:underline">
          + Add row
        </button>
      </div>
      {rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 px-3 py-2 text-xs text-slate-400">None extracted</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="min-w-full text-xs">
            <thead className="bg-slate-50">
              <tr>
                {cols.map((c) => (
                  <th key={c.key} className="whitespace-nowrap px-2 py-1.5 text-left font-semibold text-slate-500">
                    {c.label}
                  </th>
                ))}
                <th className="w-6" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => {
                    const v = r[c.key] === null || r[c.key] === undefined ? '' : String(r[c.key])
                    const cls = `w-full rounded border border-transparent bg-transparent px-1.5 py-1 text-xs hover:border-slate-200 focus:border-teal-500 focus:bg-white focus:outline-none`
                    return (
                      <td key={c.key} className="px-1 py-0.5" style={{ minWidth: c.w || '6rem' }}>
                        {c.type === 'select' ? (
                          <select className={cls} value={v} onChange={(e) => update(i, c.key, e.target.value)}>
                            {[...(c.options || []), ...(v && !(c.options || []).includes(v) ? [v] : [])].map((o) => (
                              <option key={o} value={o}>
                                {o ? titleCase(o) : '—'}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input className={cls} type={c.type === 'date' ? 'date' : 'text'} value={v} onChange={(e) => update(i, c.key, e.target.value)} />
                        )}
                      </td>
                    )
                  })}
                  <td className="px-1">
                    <button type="button" onClick={() => onChange(rows.filter((_, j) => j !== i))} className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600" title="Remove row">
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/** Normalise a possibly-partial extraction into a fully-populated editable object. */
export function normaliseExtraction(x?: ExtractionResult | null): ExtractionResult {
  const e = x || {}
  return {
    ...e,
    // An unknown type from the AI is shown (and committed) as 'other'.
    doc_type: e.doc_type && DOC_TYPES.includes(e.doc_type) ? e.doc_type : 'other',
    document_date: e.document_date || '',
    facility: e.facility || '',
    doctor_name: e.doctor_name || '',
    specialization: e.specialization || '',
    summary: e.summary || '',
    conditions: arr(e.conditions),
    medications: arr(e.medications),
    lab_results: arr(e.lab_results),
    vitals: arr(e.vitals),
    encounter: e.encounter ?? null,
    confidence: e.confidence ?? null,
    warnings: arr(e.warnings),
  }
}

const nullify = (o: Row): Row => {
  const out: Row = {}
  Object.entries(o).forEach(([k, v]) => (out[k] = typeof v === 'string' && v.trim() === '' ? null : typeof v === 'string' ? v.trim() : v))
  return out
}

/** Clean up an edited extraction before commit: '' → null, drop rows with no primary field, numeric fee. */
export function cleanExtraction(e: ExtractionResult): ExtractionResult {
  const rows = <T,>(xs: T[] | undefined, key: string) =>
    arr(xs)
      .map((r) => nullify(r as Row))
      .filter((r) => r[key]) as unknown as T[]
  let encounter: ExEncounter | null = null
  if (e.encounter) {
    const enc = nullify(e.encounter as Row) as ExEncounter
    const fee = enc.fee === null || enc.fee === undefined ? null : Number(String(enc.fee).replace(/[₹,\s]|Rs\.?|INR/gi, ''))
    encounter = { ...enc, type: enc.type || 'opd', fee: fee === null || isNaN(fee) ? null : fee }
  }
  return {
    ...e,
    doc_type: e.doc_type || 'other',
    document_date: e.document_date || null,
    facility: e.facility?.trim() || null,
    doctor_name: e.doctor_name?.trim() || null,
    specialization: e.specialization?.trim() || null,
    summary: e.summary?.trim() || null,
    conditions: rows(e.conditions, 'name'),
    medications: rows(e.medications, 'drug_name'),
    lab_results: rows(e.lab_results, 'test_name'),
    vitals: rows(e.vitals, 'kind'),
    encounter,
    warnings: arr(e.warnings),
  }
}

export default function ReviewForm({ value, onChange }: { value: ExtractionResult; onChange: (v: ExtractionResult) => void }) {
  const set = <K extends keyof ExtractionResult>(k: K, v: ExtractionResult[K]) => onChange({ ...value, [k]: v })
  const [showEnc, setShowEnc] = useState<boolean>(!!value.encounter)
  const enc = value.encounter || {}
  const setEnc = (k: keyof ExEncounter, v: string) => onChange({ ...value, encounter: { ...enc, [k]: v } })
  const conf = typeof value.confidence === 'number' ? Math.max(0, Math.min(1, value.confidence)) : null
  const warnings = arr(value.warnings)

  return (
    <div className="space-y-5">
      {(conf !== null || warnings.length > 0) && (
        <div className="space-y-2">
          {conf !== null && (
            <div className="flex items-center gap-3">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">AI confidence</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                <div className={`h-full rounded-full ${conf >= 0.75 ? 'bg-emerald-500' : conf >= 0.5 ? 'bg-amber-500' : 'bg-rose-500'}`} style={{ width: `${conf * 100}%` }} />
              </div>
              <span className="w-10 text-right text-sm font-semibold text-slate-700">{Math.round(conf * 100)}%</span>
            </div>
          )}
          {warnings.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
              <div className="mb-1 text-xs font-semibold text-amber-900">⚠ Please double-check</div>
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-900">
                {warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Document type">
          <select className={inputCls} value={value.doc_type && DOC_TYPES.includes(value.doc_type) ? value.doc_type : 'other'} onChange={(e) => set('doc_type', e.target.value)}>
            {DOC_TYPES.map((t) => (
              <option key={t} value={t}>
                {titleCase(t)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Document date">
          <input className={inputCls} type="date" value={value.document_date || ''} onChange={(e) => set('document_date', e.target.value)} />
        </Field>
        <Field label="Facility">
          <input className={inputCls} value={value.facility || ''} onChange={(e) => set('facility', e.target.value)} />
        </Field>
        <Field label="Doctor">
          <input className={inputCls} value={value.doctor_name || ''} onChange={(e) => set('doctor_name', e.target.value)} />
        </Field>
        <Field label="Specialization">
          <input className={inputCls} value={value.specialization || ''} onChange={(e) => set('specialization', e.target.value)} />
        </Field>
        <Field label="Summary" className="sm:col-span-2">
          <textarea className={`${inputCls} min-h-[64px]`} value={value.summary || ''} onChange={(e) => set('summary', e.target.value)} />
        </Field>
      </div>

      <EditableTable
        title="Medications"
        rows={arr(value.medications) as unknown as Row[]}
        onChange={(r) => set('medications', r as never)}
        blank={{ drug_name: '', dosage: '', frequency: '', route: '', duration: '', instructions: '', start_date: '' }}
        cols={[
          { key: 'drug_name', label: 'Drug', w: '9rem' },
          { key: 'dosage', label: 'Dosage', w: '5rem' },
          { key: 'frequency', label: 'Frequency', w: '5rem' },
          { key: 'route', label: 'Route', w: '4rem' },
          { key: 'duration', label: 'Duration', w: '5rem' },
          { key: 'instructions', label: 'Instructions', w: '8rem' },
          { key: 'start_date', label: 'Start', type: 'date', w: '8rem' },
        ]}
      />
      <EditableTable
        title="Lab results"
        rows={arr(value.lab_results) as unknown as Row[]}
        onChange={(r) => set('lab_results', r as never)}
        blank={{ test_name: '', value: '', unit: '', ref_range: '', flag: '', date: value.document_date || '' }}
        cols={[
          { key: 'test_name', label: 'Test', w: '7rem' },
          { key: 'value', label: 'Value', w: '4rem' },
          { key: 'unit', label: 'Unit', w: '4rem' },
          { key: 'ref_range', label: 'Ref range', w: '6rem' },
          { key: 'flag', label: 'Flag', type: 'select', options: FLAGS, w: '5rem' },
          { key: 'date', label: 'Date', type: 'date', w: '8rem' },
        ]}
      />
      <EditableTable
        title="Vitals"
        rows={arr(value.vitals) as unknown as Row[]}
        onChange={(r) => set('vitals', r as never)}
        blank={{ kind: 'bp', value: '', unit: '', date: value.document_date || '' }}
        cols={[
          { key: 'kind', label: 'Kind', type: 'select', options: VITAL_KINDS, w: '5rem' },
          { key: 'value', label: 'Value', w: '5rem' },
          { key: 'unit', label: 'Unit', w: '4rem' },
          { key: 'date', label: 'Date', type: 'date', w: '8rem' },
        ]}
      />
      <EditableTable
        title="Conditions"
        rows={arr(value.conditions) as unknown as Row[]}
        onChange={(r) => set('conditions', r as never)}
        blank={{ name: '', diagnosed_date: '', severity: '', status: 'active' }}
        cols={[
          { key: 'name', label: 'Condition', w: '10rem' },
          { key: 'diagnosed_date', label: 'Diagnosed', type: 'date', w: '8rem' },
          { key: 'severity', label: 'Severity', w: '5rem' },
          { key: 'status', label: 'Status', type: 'select', options: COND_STATUS, w: '5rem' },
        ]}
      />

      <div className="rounded-lg border border-slate-200">
        <label className="flex cursor-pointer items-center justify-between gap-2 px-3 py-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Encounter (visit / admission / bill)</span>
          <span className="flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={showEnc}
              onChange={(e) => {
                setShowEnc(e.target.checked)
                onChange({ ...value, encounter: e.target.checked ? { type: 'opd', date: value.document_date || '', ...(value.encounter || {}) } : null })
              }}
              className="accent-teal-600"
            />
            Include
          </span>
        </label>
        {showEnc && (
          <div className="grid gap-3 border-t border-slate-100 p-3 sm:grid-cols-3">
            <Field label="Type">
              <select className={inputCls} value={enc.type || 'opd'} onChange={(e) => setEnc('type', e.target.value)}>
                {ENC_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.toUpperCase()}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Date">
              <input className={inputCls} type="date" value={enc.date || ''} onChange={(e) => setEnc('date', e.target.value)} />
            </Field>
            <Field label="End date">
              <input className={inputCls} type="date" value={enc.end_date || ''} onChange={(e) => setEnc('end_date', e.target.value)} />
            </Field>
            <Field label="Reason" className="sm:col-span-3">
              <input className={inputCls} value={enc.reason || ''} onChange={(e) => setEnc('reason', e.target.value)} />
            </Field>
            <Field label="Diagnosis" className="sm:col-span-3">
              <input className={inputCls} value={enc.diagnosis || ''} onChange={(e) => setEnc('diagnosis', e.target.value)} />
            </Field>
            <Field label="Notes" className="sm:col-span-3">
              <textarea className={`${inputCls} min-h-[56px]`} value={enc.notes || ''} onChange={(e) => setEnc('notes', e.target.value)} />
            </Field>
            <Field label="Advice (diet / exercise)" className="sm:col-span-3">
              <input className={inputCls} value={enc.advice || ''} onChange={(e) => setEnc('advice', e.target.value)} />
            </Field>
            <Field label="Follow-up">
              <input className={inputCls} type="date" value={enc.follow_up_date || ''} onChange={(e) => setEnc('follow_up_date', e.target.value)} />
            </Field>
            <Field label="Fee (₹)">
              <input className={inputCls} inputMode="decimal" value={enc.fee === null || enc.fee === undefined ? '' : String(enc.fee)} onChange={(e) => setEnc('fee', e.target.value)} />
            </Field>
          </div>
        )}
      </div>
    </div>
  )
}
