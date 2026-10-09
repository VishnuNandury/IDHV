import { useMemo, useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../auth'
import { TimelineList } from '../../components/PatientSummary'
import { ErrorBox, Loading, PageHeader, Tabs, arr, useAsync } from '../../components/ui'
import type { TimelineItem } from '../../types'

type F = 'all' | 'encounter' | 'document'

export default function Timeline() {
  const { user } = useAuth()
  const pid = user!.id
  const { data, error, loading, reload } = useAsync(() => api.get<TimelineItem[]>(`/patients/${pid}/timeline`), [pid])
  const [f, setF] = useState<F>('all')
  const items = useMemo(() => arr(data).filter((t) => f === 'all' || t.kind === f), [data, f])
  return (
    <div className="max-w-3xl">
      <PageHeader title="Timeline" subtitle="Visits, admissions and documents — newest first." />
      <Tabs<F>
        value={f}
        onChange={setF}
        tabs={[
          { id: 'all', label: `All (${arr(data).length})` },
          { id: 'encounter', label: 'Encounters' },
          { id: 'document', label: 'Documents' },
        ]}
      />
      {loading && !data && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && <TimelineList items={items} />}
    </div>
  )
}
