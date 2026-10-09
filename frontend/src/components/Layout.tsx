import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import type { Role } from '../types'
import { Pill, aiBrand } from './ui'

const NAV: Record<Role, { to: string; label: string; icon: string; end?: boolean }[]> = {
  patient: [
    { to: '/patient', label: 'Overview', icon: '🩺', end: true },
    { to: '/patient/timeline', label: 'Timeline', icon: '🕑' },
    { to: '/patient/records', label: 'Records', icon: '📋' },
    { to: '/patient/upload', label: 'Upload & Digitise', icon: '⬆' },
    { to: '/patient/expenses', label: 'Expenses', icon: '₹' },
    { to: '/patient/access', label: 'Access Control', icon: '🔐' },
    { to: '/patient/profile', label: 'Profile', icon: '👤' },
  ],
  doctor: [{ to: '/doctor', label: 'My Patients', icon: '👥', end: true }],
  admin: [
    { to: '/admin', label: 'Audit Log', icon: '🧾', end: true },
    { to: '/admin/users', label: 'Users & Stats', icon: '📊' },
  ],
}

export function Logo({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-teal-600 text-white shadow-sm">
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor" aria-hidden>
          <path d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z" />
        </svg>
      </div>
      <div className="leading-tight">
        <div className="text-sm font-bold text-slate-900">IDH Vault</div>
        <div className="text-[10px] uppercase tracking-wider text-slate-500">Integrated Digital Health</div>
      </div>
    </div>
  )
}

export function AiBadge() {
  const { systemInfo } = useAuth()
  if (!systemInfo) return <Pill value="offline" label="AI: unknown" />
  const mode = systemInfo.ai_mode
  const brand = aiBrand(mode)
  const claude = brand !== null
  return (
    <span
      title={claude ? `Model: ${systemInfo.model || brand}` : 'Offline mode — set ANTHROPIC_API_KEY, XAI_API_KEY or GROQ_API_KEY for AI answers'}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        claude ? 'bg-violet-100 text-violet-700' : 'bg-slate-200 text-slate-700'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${claude ? 'bg-violet-500' : 'bg-slate-500'}`} />
      {claude ? `AI: ${brand}${systemInfo.model ? ` · ${systemInfo.model}` : ''}` : 'AI: Offline'}
    </span>
  )
}

export function DemoBanner() {
  return (
    <div className="bg-amber-400/90 px-4 py-1 text-center text-xs font-medium text-amber-950">
      Demo — synthetic data · Not for clinical use · Compliance badges (ABDM / DPDP / HIPAA) are illustrative only
    </div>
  )
}

export default function Layout() {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  if (!user) return null
  const items = NAV[user.role] || []

  const sidebar = (
    <nav className="flex flex-col gap-1 p-3">
      {items.map((i) => (
        <NavLink
          key={i.to}
          to={i.to}
          end={i.end}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
              isActive ? 'bg-teal-50 text-teal-800 ring-1 ring-teal-200' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
            }`
          }
        >
          <span className="w-5 text-center" aria-hidden>
            {i.icon}
          </span>
          {i.label}
        </NavLink>
      ))}
      <div className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-500">
        <div className="mb-1 font-semibold text-slate-600">Compliance (demo)</div>
        <div className="flex flex-wrap gap-1">
          {['ABDM', 'DPDP 2023', 'HIPAA-style'].map((b) => (
            <span key={b} className="rounded border border-slate-300 bg-white px-1.5 py-0.5 font-medium">
              {b}
            </span>
          ))}
        </div>
        <div className="mt-2">Consent-gated access · hash-chained audit log</div>
      </div>
    </nav>
  )

  return (
    <div className="flex min-h-screen flex-col">
      <DemoBanner />
      <header className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-slate-200 bg-white/95 px-3 py-2 backdrop-blur sm:px-5">
        <div className="flex items-center gap-2">
          <button className="rounded-md p-2 text-slate-600 hover:bg-slate-100 lg:hidden" onClick={() => setOpen((o) => !o)} aria-label="Menu">
            ☰
          </button>
          <Logo />
        </div>
        <div className="flex items-center gap-2 sm:gap-3">
          <AiBadge />
          <div className="hidden text-right sm:block">
            <div className="text-sm font-medium text-slate-800">{user.full_name}</div>
            <div className="text-[11px] capitalize text-slate-500">{user.role}</div>
          </div>
          <button
            onClick={() => {
              logout()
              nav('/login')
            }}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </header>
      <div className="flex flex-1">
        <aside className="hidden w-60 shrink-0 border-r border-slate-200 bg-white lg:block">{sidebar}</aside>
        {open && (
          <div className="fixed inset-0 z-40 bg-slate-900/40 lg:hidden" onClick={() => setOpen(false)}>
            <aside className="h-full w-64 bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
              <div className="border-b border-slate-200 p-3">
                <Logo />
              </div>
              {sidebar}
            </aside>
          </div>
        )}
        <main className="min-w-0 flex-1 p-3 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
