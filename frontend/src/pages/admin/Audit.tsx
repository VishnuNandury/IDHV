import { Fragment, useState } from 'react'
import { api } from '../../api'
import { Card, Empty, ErrorBox, Loading, PageHeader, Pill, Spinner, arr, btn, fmtDate, useAsync } from '../../components/ui'
import type { AuditPage, VerifyResult } from '../../types'

const PAGE = 50

const ACTION_TONE: Record<string, string> = {
  LOGIN: 'bg-slate-100 text-slate-600',
  UPLOAD: 'bg-sky-100 text-sky-700',
  COMMIT_EXTRACTION: 'bg-emerald-100 text-emerald-700',
  GRANT_CREATED: 'bg-amber-100 text-amber-800',
  GRANT_REDEEMED: 'bg-teal-100 text-teal-800',
  GRANT_REVOKED: 'bg-rose-100 text-rose-700',
  VIEW_RECORD: 'bg-indigo-50 text-indigo-700',
  ASK_QUERY: 'bg-violet-100 text-violet-700',
  WRITE_NOTE: 'bg-fuchsia-100 text-fuchsia-700',
}

export default function Audit() {
  const [offset, setOffset] = useState(0)
  const { data, error, loading, reload } = useAsync(() => api.get<AuditPage>(`/admin/audit?limit=${PAGE}&offset=${offset}`), [offset])
  const [verify, setVerify] = useState<VerifyResult | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [verr, setVerr] = useState<unknown>(null)
  const [expanded, setExpanded] = useState<number | null>(null)

  const runVerify = async () => {
    setVerifying(true)
    setVerr(null)
    try {
      setVerify(await api.get<VerifyResult>('/admin/audit/verify'))
    } catch (e) {
      setVerr(e)
    } finally {
      setVerifying(false)
    }
  }

  const items = arr(data?.items)
  const total = data?.total ?? items.length

  return (
    <div>
      <PageHeader
        title="Audit log"
        subtitle="Append-only, SHA-256 hash-chained. Every login, upload, consent event and doctor access is recorded."
        action={
          <button className={btn.primary} onClick={runVerify} disabled={verifying}>
            {verifying ? <Spinner className="h-4 w-4 text-white" /> : '🔗'} Verify chain
          </button>
        }
      />
      <ErrorBox error={verr} />
      {verify && (
        <div
          className={`mb-4 flex items-center gap-3 rounded-xl border px-4 py-3 text-sm ${
            verify.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-rose-300 bg-rose-50 text-rose-800'
          }`}
        >
          <span className="text-2xl">{verify.ok ? '✅' : '⛔'}</span>
          <div>
            <div className="font-semibold">{verify.ok ? 'Chain intact' : 'Tampering detected'}</div>
            <div className="text-xs">
              {verify.checked ?? '?'} entries checked
              {!verify.ok && verify.broken_id != null && ` · first broken entry #${verify.broken_id}`}
            </div>
          </div>
        </div>
      )}
      <Card bodyClass="p-0" title={`${total} entries`} action={<button className={btn.ghost} onClick={reload}>↻ Refresh</button>}>
        {loading && !data && <Loading />}
        {error ? (
          <div className="p-4">
            <ErrorBox error={error} onRetry={reload} />
          </div>
        ) : null}
        {data && items.length === 0 && (
          <div className="p-4">
            <Empty>No audit entries</Empty>
          </div>
        )}
        {items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {['#', 'Time', 'Actor', 'Action', 'Patient', 'IP', 'Hash'].map((h) => (
                    <th key={h} className="whitespace-nowrap px-3 py-2 text-left font-semibold">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((a) => {
                  const broken = verify && !verify.ok && verify.broken_id === a.id
                  return (
                    <Fragment key={a.id}>
                      <tr className={`cursor-pointer hover:bg-slate-50 ${broken ? 'bg-rose-50' : ''}`} onClick={() => setExpanded(expanded === a.id ? null : a.id)}>
                        <td className="px-3 py-2 font-mono text-xs text-slate-400">{a.id}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-slate-600">{fmtDate(a.ts, true)}</td>
                        <td className="px-3 py-2">
                          <div className="text-slate-800">{a.actor_name || a.actor_email || (a.actor_id ? `User #${a.actor_id}` : 'system')}</div>
                          {a.actor_role && <Pill value={a.actor_role} />}
                        </td>
                        <td className="px-3 py-2">
                          <span className={`rounded-md px-2 py-0.5 font-mono text-[11px] font-semibold ${ACTION_TONE[a.action] || 'bg-slate-100 text-slate-600'}`}>{a.action}</span>
                        </td>
                        <td className="px-3 py-2 text-slate-600">{a.patient_id ?? '—'}</td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-500">{a.ip || '—'}</td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-400" title={a.hash || ''}>
                          {a.hash ? `${a.hash.slice(0, 10)}…` : '—'}
                        </td>
                      </tr>
                      {expanded === a.id && (
                        <tr className="bg-slate-50">
                          <td colSpan={7} className="px-3 py-2">
                            <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-slate-900 p-3 font-mono text-[11px] text-slate-100">
                              {JSON.stringify({ detail: a.detail ?? null, hash: a.hash }, null, 2)}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {total > PAGE && (
          <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2 text-sm text-slate-500">
            <span>
              {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
            </span>
            <div className="flex gap-2">
              <button className={btn.secondary + ' !py-1'} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                ← Newer
              </button>
              <button className={btn.secondary + ' !py-1'} disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
                Older →
              </button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
