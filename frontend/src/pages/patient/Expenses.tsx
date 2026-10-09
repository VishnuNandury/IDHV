import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../../api'
import { useAuth } from '../../auth'
import { C } from '../../components/Charts'
import { Card, Empty, ErrorBox, Loading, PageHeader, Stat, arr, fmtINR, useAsync } from '../../components/ui'
import type { Expenses as ExpensesT } from '../../types'

const TYPES: { key: 'opd' | 'ipd' | 'diagnostic' | 'pharmacy'; label: string }[] = [
  { key: 'opd', label: 'OPD consults' },
  { key: 'ipd', label: 'Hospital (IPD)' },
  { key: 'diagnostic', label: 'Diagnostics' },
  { key: 'pharmacy', label: 'Pharmacy' },
]

const monthLabel = (m: string) => {
  const d = new Date(m.length === 7 ? m + '-01T00:00:00' : m)
  return isNaN(d.getTime()) ? m : d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' })
}
const kTick = (v: number) => (v >= 1000 ? `₹${Math.round(v / 1000)}k` : `₹${v}`)

export default function Expenses() {
  const { user } = useAuth()
  const pid = user!.id
  const { data, error, loading, reload } = useAsync(() => api.get<ExpensesT>(`/patients/${pid}/expenses`), [pid])
  const byType = TYPES.map((t) => ({ name: t.label, amount: Number(data?.by_type?.[t.key] || 0) }))
  const byMonth = arr(data?.by_month)
    .map((m) => ({ ...m, amount: Number(m.amount) || 0 }))
    .sort((a, b) => (a.month < b.month ? -1 : 1))
  const total = Number(data?.total ?? byType.reduce((s, x) => s + x.amount, 0)) || 0

  return (
    <div>
      <PageHeader title="Healthcare expenses" subtitle="Consultation fees, hospital bills, diagnostics and pharmacy — from your committed documents." />
      {loading && !data && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Total spend" value={fmtINR(total)} />
            {TYPES.map((t) => (
              <Stat key={t.key} label={t.label} value={fmtINR(data.by_type?.[t.key] || 0)} tone="slate" />
            ))}
          </div>
          <div className="grid gap-5 lg:grid-cols-3">
            <Card title="Spend by month" className="lg:col-span-2">
              {byMonth.length === 0 ? (
                <Empty>No expenses recorded</Empty>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={byMonth} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke={C.grid} vertical={false} />
                    <XAxis dataKey="month" tickFormatter={monthLabel} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={{ stroke: C.grid }} minTickGap={8} />
                    <YAxis tickFormatter={kTick} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} width={50} />
                    <Tooltip cursor={{ fill: '#f1f5f9' }} labelFormatter={(m) => monthLabel(String(m))} formatter={(v) => [fmtINR(Number(v)), 'Spend']} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                    <Bar dataKey="amount" fill={C.teal} radius={[4, 4, 0, 0]} maxBarSize={36} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Card>
            <Card title="Spend by type">
              {total === 0 ? (
                <Empty>No expenses recorded</Empty>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={byType} layout="vertical" margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
                    <CartesianGrid stroke={C.grid} horizontal={false} />
                    <XAxis type="number" tickFormatter={kTick} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} />
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} width={96} />
                    <Tooltip cursor={{ fill: '#f1f5f9' }} formatter={(v) => [fmtINR(Number(v)), 'Spend']} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                    <Bar dataKey="amount" fill={C.teal} radius={[0, 4, 4, 0]} maxBarSize={28} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
