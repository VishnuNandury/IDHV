import { api } from '../../api'
import { Card, Empty, ErrorBox, Loading, PageHeader, Pill, Stat, arr, fmtDate, titleCase, useAsync } from '../../components/ui'
import type { AdminStats, User } from '../../types'

export default function Users() {
  const stats = useAsync(() => api.get<unknown>('/admin/stats'), [])
  const users = useAsync(() => api.get<User[]>('/admin/users'), [])
  const s = stats.data ? normaliseStats(stats.data) : null
  const byStatus = Object.entries(s?.documents_by_status || {})
  const docTotal = byStatus.reduce((a, [, v]) => a + (Number(v) || 0), 0) || 1

  return (
    <div>
      <PageHeader title="Users & platform stats" subtitle="Anonymised aggregate counts — admins see no clinical content." />
      <ErrorBox error={stats.error} onRetry={stats.reload} />
      {stats.loading && !s && <Loading />}
      {s && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Patients" value={s.patients ?? 0} />
            <Stat label="Doctors" value={s.doctors ?? 0} tone="indigo" />
            <Stat label="Documents" value={s.documents ?? 0} tone="slate" />
            <Stat label="Active grants" value={s.active_grants ?? 0} tone="amber" />
            <Stat label="AI queries" value={s.queries_last_7d ?? 0} tone="teal" />
          </div>
          {byStatus.length > 0 && (
            <Card title="Documents by pipeline status">
              <div className="space-y-2">
                {byStatus.map(([k, v]) => (
                  <div key={k} className="flex items-center gap-3 text-sm">
                    <div className="w-28">
                      <Pill value={k} />
                    </div>
                    <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full rounded-full bg-teal-500" style={{ width: `${((Number(v) || 0) / docTotal) * 100}%` }} />
                    </div>
                    <div className="w-10 text-right font-semibold text-slate-700">{v}</div>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}
      <Card className="mt-5" title={`Users (${arr(users.data).length})`} bodyClass="p-0">
        {users.loading && !users.data && <Loading />}
        {users.error ? (
          <div className="p-4">
            <ErrorBox error={users.error} onRetry={users.reload} />
          </div>
        ) : null}
        {users.data && arr(users.data).length === 0 && (
          <div className="p-4">
            <Empty>No users</Empty>
          </div>
        )}
        {arr(users.data).length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {['#', 'Name', 'Email', 'Role', 'Joined'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left font-semibold">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {arr(users.data).map((u) => (
                  <tr key={u.id}>
                    <td className="px-4 py-2 font-mono text-xs text-slate-400">{u.id}</td>
                    <td className="px-4 py-2 font-medium text-slate-800">{u.full_name}</td>
                    <td className="px-4 py-2 text-slate-600">{u.email}</td>
                    <td className="px-4 py-2">
                      <Pill value={u.role} label={titleCase(u.role)} />
                    </td>
                    <td className="px-4 py-2 text-slate-600">{fmtDate(u.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

/** Accepts the flat contract shape or the backend's nested one ({users:{patient}, documents:{total, by_status}, grants:{active}, audit_events}). */
function normaliseStats(raw: unknown): AdminStats {
  const r = (raw || {}) as Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any
  const docs = r.documents
  return {
    patients: r.patients ?? r.users?.patient ?? 0,
    doctors: r.doctors ?? r.users?.doctor ?? 0,
    documents: typeof docs === 'number' ? docs : docs?.total ?? 0,
    documents_by_status: r.documents_by_status ?? (typeof docs === 'object' ? docs?.by_status : undefined) ?? {},
    active_grants: r.active_grants ?? r.grants?.active ?? 0,
    queries_last_7d: r.queries_last_7d ?? r.audit_events?.ASK_QUERY ?? 0,
  }
}
