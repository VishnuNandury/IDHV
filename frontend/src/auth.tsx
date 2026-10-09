import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, getStoredUser, getToken, onUnauthorized, setStoredUser, setToken } from './api'
import type { LoginResponse, Role, SystemInfo, User } from './types'

interface AuthState {
  user: User | null
  ready: boolean
  login: (email: string, password: string) => Promise<User>
  register: (data: Record<string, unknown>) => Promise<User>
  logout: () => void
  systemInfo: SystemInfo | null
}

const AuthCtx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => (getToken() ? getStoredUser<User>() : null))
  const [ready, setReady] = useState(false)
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)

  const logout = useCallback(() => {
    setToken(null)
    setStoredUser(null)
    setUser(null)
  }, [])

  useEffect(() => {
    onUnauthorized(logout)
    return () => onUnauthorized(null)
  }, [logout])

  // Validate any stored token on boot.
  useEffect(() => {
    let cancelled = false
    if (!getToken()) {
      setReady(true)
      return
    }
    api
      .get<User>('/auth/me')
      .then((u) => {
        if (cancelled) return
        setUser(u)
        setStoredUser(u)
      })
      .catch(() => {
        if (!cancelled) logout()
      })
      .finally(() => !cancelled && setReady(true))
    return () => {
      cancelled = true
    }
  }, [logout])

  useEffect(() => {
    api
      .get<SystemInfo>('/system/info')
      .then(setSystemInfo)
      .catch(() => setSystemInfo(null))
  }, [])

  const finish = (r: LoginResponse) => {
    setToken(r.access_token)
    setStoredUser(r.user)
    setUser(r.user)
    return r.user
  }

  const login = useCallback(async (email: string, password: string) => {
    const r = await api.post<LoginResponse>('/auth/login', { email, password })
    return finish(r)
  }, [])

  const register = useCallback(async (data: Record<string, unknown>) => {
    const r = await api.post<LoginResponse | User>('/auth/register', data)
    if (r && typeof r === 'object' && 'access_token' in r) return finish(r)
    // Backend returned just the user — log in with the same credentials.
    const lr = await api.post<LoginResponse>('/auth/login', { email: data.email, password: data.password })
    return finish(lr)
  }, [])

  const value = useMemo(
    () => ({ user, ready, login, register, logout, systemInfo }),
    [user, ready, login, register, logout, systemInfo],
  )
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}

export function homeFor(role: Role | undefined): string {
  if (role === 'doctor') return '/doctor'
  if (role === 'admin') return '/admin'
  return '/patient'
}
