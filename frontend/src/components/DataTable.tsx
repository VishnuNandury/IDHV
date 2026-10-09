import type { ReactNode } from 'react'
import { SourceLink } from './DocumentPreview'
import { Empty, Pill, fmtDate, fmtINR, titleCase } from './ui'

type Row = Record<string, unknown>

export interface Column {
  key: string
  label: string
  render?: (row: Row) => ReactNode
  className?: string
}

const HIDDEN = new Set(['id', 'patient_id', 'source_document_id', 'source_page', 'source_filename', 'created_at', 'updated_at'])

const s = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : String(v))

/** Known column sets per record kind; unknown kinds fall back to auto-detected columns. */
export const RECORD_COLUMNS: Record<string, Column[]> = {
  conditions: [
    { key: 'name', label: 'Condition', className: 'font-medium text-slate-800' },
    { key: 'diagnosed_date', label: 'Diagnosed', render: (r) => fmtDate(r.diagnosed_date as string) },
    { key: 'severity', label: 'Severity' },
    { key: 'status', label: 'Status', render: (r) => <Pill value={r.status as string} /> },
  ],
  medications: [
    { key: 'drug_name', label: 'Drug', className: 'font-medium text-slate-800' },
    { key: 'dosage', label: 'Dosage' },
    { key: 'frequency', label: 'Frequency' },
    { key: 'route', label: 'Route' },
    { key: 'duration', label: 'Duration' },
    { key: 'start_date', label: 'Start', render: (r) => fmtDate(r.start_date as string) },
    { key: 'end_date', label: 'End', render: (r) => fmtDate(r.end_date as string) },
    { key: 'prescriber', label: 'Prescriber' },
    {
      key: 'active',
      label: 'Status',
      render: (r) => (r.active === false ? <Pill value="resolved" label="Stopped" /> : <Pill value="active" />),
    },
  ],
  labs: [
    { key: 'date', label: 'Date', render: (r) => fmtDate(r.date as string) },
    { key: 'test_name', label: 'Test', className: 'font-medium text-slate-800' },
    {
      key: 'value',
      label: 'Result',
      render: (r) => (
        <span className={r.flag === 'high' || r.flag === 'low' ? 'font-semibold text-rose-600' : ''}>
          {s(r.value ?? r.value_num)} <span className="text-xs text-slate-400">{(r.unit as string) || ''}</span>
        </span>
      ),
    },
    { key: 'ref_range', label: 'Ref. range' },
    { key: 'flag', label: 'Flag', render: (r) => (r.flag ? <Pill value={r.flag as string} /> : '—') },
    { key: 'lab_name', label: 'Lab' },
  ],
  vitals: [
    { key: 'date', label: 'Date', render: (r) => fmtDate(r.date as string) },
    { key: 'kind', label: 'Vital', render: (r) => <span className="font-medium uppercase">{s(r.kind)}</span> },
    { key: 'value', label: 'Value', render: (r) => `${s(r.value ?? r.value_num)} ${(r.unit as string) || ''}` },
  ],
  encounters: [
    { key: 'date', label: 'Date', render: (r) => fmtDate(r.date as string) },
    { key: 'type', label: 'Type', render: (r) => <span className="font-semibold uppercase text-slate-700">{s(r.type)}</span> },
    { key: 'facility', label: 'Facility' },
    { key: 'doctor_name', label: 'Doctor' },
    { key: 'reason', label: 'Reason' },
    { key: 'diagnosis', label: 'Diagnosis', className: 'text-slate-800' },
    { key: 'follow_up_date', label: 'Follow-up', render: (r) => fmtDate(r.follow_up_date as string) },
    { key: 'fee', label: 'Fee', render: (r) => fmtINR(r.fee as number) },
  ],
}

function autoColumns(rows: Row[]): Column[] {
  const keys = new Set<string>()
  rows.slice(0, 20).forEach((r) => Object.keys(r).forEach((k) => !HIDDEN.has(k) && keys.add(k)))
  return [...keys].map((k) => ({ key: k, label: titleCase(k) }))
}

export default function DataTable({ rows, kind, columns, empty = 'No records' }: { rows: Row[]; kind?: string; columns?: Column[]; empty?: string }) {
  if (!rows || rows.length === 0) return <Empty>{empty}</Empty>
  let cols = columns || (kind && RECORD_COLUMNS[kind]) || autoColumns(rows)
  // Drop known columns that no row populates (keeps tables tight), but always keep the first.
  cols = cols.filter((c, i) => i === 0 || c.render || rows.some((r) => r[c.key] !== null && r[c.key] !== undefined && r[c.key] !== ''))
  const hasSource = rows.some((r) => r.source_document_id)
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50">
          <tr>
            {cols.map((c) => (
              <th key={c.key} className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                {c.label}
              </th>
            ))}
            {hasSource && <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Source</th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {rows.map((r, i) => (
            <tr key={(r.id as number) ?? i} className="hover:bg-slate-50/60">
              {cols.map((c) => (
                <td key={c.key} className={`px-3 py-2 align-top text-slate-600 ${c.className || ''}`}>
                  {c.render ? c.render(r) : typeof r[c.key] === 'object' && r[c.key] !== null ? JSON.stringify(r[c.key]) : s(r[c.key])}
                </td>
              ))}
              {hasSource && (
                <td className="px-3 py-2 align-top">
                  <SourceLink docId={r.source_document_id as number} page={r.source_page as number} filename={r.source_filename as string} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
