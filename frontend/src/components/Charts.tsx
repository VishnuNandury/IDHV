import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { BpPoint, Overview, TrendPoint } from '../types'
import { Empty, fmtDate } from './ui'

// Validated palette (dataviz validator, light surface): teal + indigo pass CVD/normal separation.
export const C = { teal: '#0d9488', indigo: '#6366f1', grid: '#e2e8f0', axis: '#64748b', ref: '#d97706' }

// Clinical reference thresholds for key tests (upper limits) – used as dashed reference lines.
const REF: Record<string, { y: number; label: string }> = {
  HbA1c: { y: 7, label: 'target 7%' },
  FBS: { y: 100, label: '100' },
  LDL: { y: 100, label: '100' },
  Creatinine: { y: 1.2, label: '1.2' },
}

const shortDate = (d: string) => {
  const dt = new Date(d.length === 10 ? d + 'T00:00:00' : d)
  return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' })
}

const sortByDate = <T extends { date: string }>(xs: T[]) =>
  [...xs].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))

export function LabTrendChart({ name, points, height = 180 }: { name: string; points: TrendPoint[]; height?: number }) {
  const data = sortByDate(points.filter((p) => p && p.date && typeof p.value_num === 'number'))
  if (data.length === 0) return <Empty>No {name} results yet</Empty>
  const unit = data.find((p) => p.unit)?.unit || ''
  const last = data[data.length - 1]
  const first = data[0]
  const ref = REF[name]
  const delta = data.length > 1 && last.value_num !== null && first.value_num !== null ? last.value_num - first.value_num : null
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <div className="text-sm font-semibold text-slate-700">{name}</div>
        <div className="text-right">
          <span className="text-lg font-semibold text-slate-900">{last.value_num}</span>
          <span className="ml-1 text-xs text-slate-500">{unit}</span>
          {last.flag && last.flag !== 'normal' && (
            <span className="ml-2 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-rose-600">▲ {last.flag}</span>
          )}
          {delta !== null && (
            <div className="text-[11px] text-slate-500">
              {delta > 0 ? '↑' : delta < 0 ? '↓' : '→'} {Math.abs(delta).toFixed(2).replace(/\.?0+$/, '')} since {shortDate(first.date)}
            </div>
          )}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={{ stroke: C.grid }} minTickGap={16} />
          <YAxis tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={44} />
          {ref && (
            <ReferenceLine y={ref.y} stroke={C.ref} strokeDasharray="4 4" label={{ value: ref.label, position: 'insideTopRight', fontSize: 10, fill: '#92400e' }} />
          )}
          <Tooltip
            labelFormatter={(d) => fmtDate(String(d))}
            formatter={(v) => [`${v} ${unit}`, name]}
            contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: C.grid }}
          />
          <Line type="monotone" dataKey="value_num" name={name} stroke={C.teal} strokeWidth={2} dot={{ r: 4, strokeWidth: 2, fill: '#fff' }} activeDot={{ r: 6 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

/** Normalise bp_series: accepts {systolic, diastolic} or {value: "140/90"}. */
export function normaliseBp(series: Overview['bp_series'] | undefined): BpPoint[] {
  if (!Array.isArray(series)) return []
  const out: BpPoint[] = []
  for (const p of series) {
    if (!p || !p.date) continue
    let s: number | null = typeof p.systolic === 'number' ? p.systolic : null
    let d: number | null = typeof p.diastolic === 'number' ? p.diastolic : null
    const v = (p as { value?: string }).value
    if ((s === null || d === null) && typeof v === 'string') {
      const m = v.match(/(\d{2,3})\s*\/\s*(\d{2,3})/)
      if (m) {
        s = Number(m[1])
        d = Number(m[2])
      }
    }
    if (s !== null || d !== null) out.push({ date: p.date, systolic: s, diastolic: d })
  }
  return sortByDate(out)
}

export function BpChart({ series, height = 200 }: { series: BpPoint[]; height?: number }) {
  if (series.length === 0) return <Empty>No blood pressure readings yet</Empty>
  const last = series[series.length - 1]
  const high = (last.systolic ?? 0) >= 140 || (last.diastolic ?? 0) >= 90
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <div className="text-sm font-semibold text-slate-700">Blood pressure</div>
        <div>
          <span className="text-lg font-semibold text-slate-900">
            {last.systolic ?? '–'}/{last.diastolic ?? '–'}
          </span>
          <span className="ml-1 text-xs text-slate-500">mmHg</span>
          {high && <span className="ml-2 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-rose-600">▲ high</span>}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={{ stroke: C.grid }} minTickGap={16} />
          <YAxis tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} domain={[60, 'auto']} width={44} />
          <ReferenceLine y={140} stroke={C.ref} strokeDasharray="4 4" label={{ value: '140', position: 'insideTopRight', fontSize: 10, fill: '#92400e' }} />
          <ReferenceLine y={90} stroke={C.ref} strokeDasharray="4 4" label={{ value: '90', position: 'insideTopRight', fontSize: 10, fill: '#92400e' }} />
          <Tooltip labelFormatter={(d) => fmtDate(String(d))} formatter={(v, n) => [`${v} mmHg`, n]} contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: C.grid }} />
          <Legend iconType="plainline" wrapperStyle={{ fontSize: 11, color: C.axis }} />
          <Line type="monotone" dataKey="systolic" name="Systolic" stroke={C.teal} strokeWidth={2} dot={{ r: 4, strokeWidth: 2, fill: '#fff' }} connectNulls isAnimationActive={false} />
          <Line type="monotone" dataKey="diastolic" name="Diastolic" stroke={C.indigo} strokeWidth={2} strokeDasharray="5 3" dot={{ r: 4, strokeWidth: 2, fill: '#fff' }} connectNulls isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
