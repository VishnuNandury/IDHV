import { Link } from 'react-router-dom'
import { useAuth } from '../../auth'
import { ConditionsCard, MedicationsCard, ProfileCard, TrendsGrid, VitalsStrip, fetchOverview } from '../../components/PatientSummary'
import { ErrorBox, Loading, PageHeader, Stat, btn, useAsync } from '../../components/ui'

export default function Overview() {
  const { user } = useAuth()
  const pid = user!.id
  const { data, error, loading, reload } = useAsync(() => fetchOverview(pid), [pid])

  return (
    <div>
      <PageHeader
        title={`Hello, ${user?.full_name?.split(' ')[0] || 'there'}`}
        subtitle="Your health at a glance — everything below comes from your digitised documents."
        action={
          <Link to="/patient/upload" className={btn.primary}>
            ⬆ Upload documents
          </Link>
        }
      />
      {loading && !data && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {data && (
        <div className="space-y-5">
          <div className="grid gap-5 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <ProfileCard profile={data.profile} name={user?.full_name} />
            </div>
            <div className="grid grid-cols-3 gap-3 xl:grid-cols-1">
              <Stat label="Documents" value={data.counts?.documents ?? 0} />
              <Stat label="Encounters" value={data.counts?.encounters ?? 0} tone="indigo" />
              <Stat label="Lab results" value={data.counts?.labs ?? 0} tone="slate" />
            </div>
          </div>
          <VitalsStrip vitals={data.latest_vitals} />
          <div className="grid gap-5 lg:grid-cols-2">
            <ConditionsCard overview={data} />
            <MedicationsCard overview={data} />
          </div>
          <div>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Key lab trends</h2>
            <TrendsGrid overview={data} cols="md:grid-cols-2 xl:grid-cols-3" />
          </div>
        </div>
      )}
    </div>
  )
}
