import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { homeFor, useAuth } from './auth'
import Layout from './components/Layout'
import { Loading } from './components/ui'
import Login from './pages/Login'
import Register from './pages/Register'
import PatientOverview from './pages/patient/Overview'
import PatientTimeline from './pages/patient/Timeline'
import PatientRecords from './pages/patient/Records'
import Upload from './pages/patient/Upload'
import Expenses from './pages/patient/Expenses'
import Access from './pages/patient/Access'
import Profile from './pages/patient/Profile'
import DoctorPatients from './pages/doctor/Patients'
import Workspace from './pages/doctor/Workspace'
import Audit from './pages/admin/Audit'
import Users from './pages/admin/Users'
import type { Role } from './types'

function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { user, ready } = useAuth()
  const loc = useLocation()
  if (!user && !ready) return <Loading label="Restoring session…" />
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname }} />
  if (user.role !== role) return <Navigate to={homeFor(user.role)} replace />
  return <>{children}</>
}

function Home() {
  const { user, ready } = useAuth()
  if (!user && !ready) return <Loading />
  return <Navigate to={user ? homeFor(user.role) : '/login'} replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      <Route element={<RequireRole role="patient"><Layout /></RequireRole>}>
        <Route path="/patient" element={<PatientOverview />} />
        <Route path="/patient/timeline" element={<PatientTimeline />} />
        <Route path="/patient/records" element={<PatientRecords />} />
        <Route path="/patient/upload" element={<Upload />} />
        <Route path="/patient/expenses" element={<Expenses />} />
        <Route path="/patient/access" element={<Access />} />
        <Route path="/patient/profile" element={<Profile />} />
      </Route>

      <Route element={<RequireRole role="doctor"><Layout /></RequireRole>}>
        <Route path="/doctor" element={<DoctorPatients />} />
        <Route path="/doctor/patients/:id" element={<Workspace />} />
      </Route>

      <Route element={<RequireRole role="admin"><Layout /></RequireRole>}>
        <Route path="/admin" element={<Audit />} />
        <Route path="/admin/users" element={<Users />} />
      </Route>

      <Route path="*" element={<Home />} />
    </Routes>
  )
}
