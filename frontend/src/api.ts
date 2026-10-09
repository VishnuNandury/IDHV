// Thin fetch wrapper: bearer token from localStorage, JSON errors ({detail}),
// 401 -> global logout, 403 CONSENT_REQUIRED -> ConsentRequiredError.

const TOKEN_KEY = 'idhv_token'
const USER_KEY = 'idhv_user'

export const storage = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(key)
    } catch {
      return null
    }
  },
  set(key: string, value: string) {
    try {
      window.localStorage.setItem(key, value)
    } catch {
      /* storage unavailable (private mode etc.) */
    }
  },
  remove(key: string) {
    try {
      window.localStorage.removeItem(key)
    } catch {
      /* ignore */
    }
  },
}

export const getToken = () => storage.get(TOKEN_KEY)
export const setToken = (t: string | null) => (t ? storage.set(TOKEN_KEY, t) : storage.remove(TOKEN_KEY))
export const getStoredUser = <T,>(): T | null => {
  const raw = storage.get(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}
export const setStoredUser = (u: unknown | null) =>
  u ? storage.set(USER_KEY, JSON.stringify(u)) : storage.remove(USER_KEY)

export const CONSENT_MESSAGE = 'Access expired — ask the patient for a new OTP'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export class ConsentRequiredError extends ApiError {
  constructor() {
    super(403, CONSENT_MESSAGE, 'CONSENT_REQUIRED')
    this.name = 'ConsentRequiredError'
  }
}

export const isConsentError = (e: unknown): e is ConsentRequiredError => e instanceof ConsentRequiredError

export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

let unauthorizedHandler: (() => void) | null = null
export function onUnauthorized(fn: (() => void) | null) {
  unauthorizedHandler = fn
}

function detailToMessage(detail: unknown, fallback: string): string {
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors
    return detail
      .map((d) => {
        if (d && typeof d === 'object' && 'msg' in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.filter((x) => x !== 'body').join('.')
            : ''
          return `${loc ? loc + ': ' : ''}${(d as { msg: string }).msg}`
        }
        return JSON.stringify(d)
      })
      .join('; ')
  }
  try {
    return JSON.stringify(detail)
  } catch {
    return fallback
  }
}

async function raw(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {})
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let res: Response
  try {
    res = await fetch(`/api${path}`, { ...init, headers })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Is the backend running?')
  }
  if (res.ok) return res

  let detail: unknown = undefined
  try {
    const body = await res.clone().json()
    detail = body?.detail
  } catch {
    /* non-JSON error */
  }
  if (res.status === 401 && !path.startsWith('/auth/login')) {
    if (unauthorizedHandler) unauthorizedHandler()
    throw new ApiError(401, 'Session expired — please sign in again', detail)
  }
  if (res.status === 403 && detail === 'CONSENT_REQUIRED') {
    throw new ConsentRequiredError()
  }
  throw new ApiError(res.status, detailToMessage(detail, `${res.status} ${res.statusText || 'Request failed'}`), detail)
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await raw(path, init)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    return text as unknown as T
  }
}

const body = (method: string, data?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: data === undefined ? undefined : JSON.stringify(data),
})

export const api = {
  get: <T,>(path: string) => json<T>(path),
  post: <T,>(path: string, data?: unknown) => json<T>(path, body('POST', data ?? {})),
  put: <T,>(path: string, data?: unknown) => json<T>(path, body('PUT', data ?? {})),
  del: <T,>(path: string) => json<T>(path, { method: 'DELETE' }),
  upload: <T,>(path: string, files: File[], field = 'files') => {
    const fd = new FormData()
    files.forEach((f) => fd.append(field, f, f.name))
    return json<T>(path, { method: 'POST', body: fd })
  },
  /** Fetch a binary resource with auth and return blob + content type. */
  blob: async (path: string): Promise<{ blob: Blob; type: string }> => {
    const res = await raw(path)
    const blob = await res.blob()
    return { blob, type: res.headers.get('Content-Type') || blob.type || '' }
  },
}
