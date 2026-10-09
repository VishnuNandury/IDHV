import { useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../auth'
import DataTable from '../../components/DataTable'
import { ErrorBox, Loading, PageHeader, Tabs, arr, useAsync } from '../../components/ui'

export type RecordKind = 'conditions' | 'medications' | 'labs' | 'vitals' | 'encounters'
export const RECORD_TABS: { id: RecordKind; label: string }[] = [
  { id: 'conditions', label: 'Conditions' },
  { id: 'medications', label: 'Medications' },
  { id: 'labs', label: 'Lab results' },
  { id: 'vitals', label: 'Vitals' },
  { id: 'encounters', label: 'Encounters' },
]

/** Tabbed structured records for any patient (reused by the doctor workspace). */
export function RecordsPanel({ patientId }: { patientId: number }) {
  const [tab, setTab] = useState<RecordKind>('conditions')
  const { data, error, loading, reload } = useAsync(
    () => api.get<Record<string, unknown>[]>(`/patients/${patientId}/records/${tab}`),
    [patientId, tab],
  )
  return (
    <div>
      <Tabs<RecordKind> tabs={RECORD_TABS} value={tab} onChange={setTab} />
      <ErrorBox error={error} onRetry={reload} />
      {loading ? <Loading /> : !error && <DataTable rows={arr(data)} kind={tab} empty={`No ${tab} recorded yet`} />}
    </div>
  )
}

export default function Records() {
  const { user } = useAuth()
  return (
    <div>
      <PageHeader title="Health records" subtitle="Structured data extracted from your documents. 📎 opens the source file." />
      <RecordsPanel patientId={user!.id} />
    </div>
  )
}
