// Shared API types. All fields are treated defensively — the backend may omit any of them.

export type Role = 'patient' | 'doctor' | 'admin'

export interface User {
  id: number
  email: string
  full_name: string
  role: Role
  created_at?: string
}

export interface LoginResponse {
  access_token: string
  user: User
}

export interface HereditaryItem {
  relation: string
  condition: string
}
export interface FamilyMember {
  name: string
  relation: string
}
export interface Insurance {
  provider?: string | null
  policy_number?: string | null
  coverage?: string | number | null
}

export interface PatientProfile {
  user_id: number
  full_name?: string
  dhid?: string
  dob?: string | null
  sex?: string | null
  phone?: string | null
  address?: string | null
  occupation?: string | null
  blood_group?: string | null
  height_cm?: number | null
  weight_kg?: number | null
  bmi?: number | null
  allergies?: string[]
  hereditary_history?: HereditaryItem[]
  insurance?: Insurance | null
  family_members?: FamilyMember[]
  emergency_contact?: string | null
}

export interface SourceRef {
  source_document_id?: number | null
  source_page?: number | null
  source_filename?: string | null
}

export interface Condition extends SourceRef {
  id?: number
  name: string
  diagnosed_date?: string | null
  severity?: string | null
  status?: string | null
}

export interface Medication extends SourceRef {
  id?: number
  drug_name: string
  dosage?: string | null
  frequency?: string | null
  route?: string | null
  duration?: string | null
  start_date?: string | null
  end_date?: string | null
  prescriber?: string | null
  active?: boolean
  instructions?: string | null
}

export interface LabResult extends SourceRef {
  id?: number
  test_name: string
  value?: string | null
  value_num?: number | null
  unit?: string | null
  ref_range?: string | null
  flag?: string | null
  date?: string | null
  lab_name?: string | null
}

export interface Vital extends SourceRef {
  id?: number
  date?: string | null
  kind: string
  value?: string | null
  value_num?: number | null
  unit?: string | null
}

export interface Encounter extends SourceRef {
  id?: number
  type?: string | null
  date?: string | null
  end_date?: string | null
  facility?: string | null
  doctor_name?: string | null
  specialization?: string | null
  reason?: string | null
  diagnosis?: string | null
  notes?: string | null
  advice?: string | null
  follow_up_date?: string | null
  fee?: number | string | null
}

export interface TrendPoint {
  date: string
  value_num: number | null
  unit?: string | null
  flag?: string | null
}

export interface BpPoint {
  date: string
  systolic: number | null
  diastolic: number | null
}

export interface Overview {
  profile?: PatientProfile
  active_conditions?: Condition[]
  active_medications?: Medication[]
  latest_vitals?: Vital[]
  lab_trends?: Record<string, TrendPoint[]>
  bp_series?: Array<BpPoint | { date: string; value?: string; systolic?: number; diastolic?: number }>
  counts?: { documents?: number; encounters?: number; labs?: number }
}

export interface TimelineItem {
  date?: string | null
  kind: 'encounter' | 'document' | string
  type?: string | null
  title?: string | null
  subtitle?: string | null
  id?: number
  source_document_id?: number | null
}

export interface Expenses {
  total?: number
  by_type?: Partial<Record<'opd' | 'ipd' | 'diagnostic' | 'pharmacy', number>>
  by_month?: { month: string; amount: number }[]
}

// --- Extraction (spec §5) ---
export interface ExCondition {
  name: string
  diagnosed_date?: string | null
  severity?: string | null
  status?: string | null
  page?: number | null
}
export interface ExMedication {
  drug_name: string
  dosage?: string | null
  frequency?: string | null
  route?: string | null
  duration?: string | null
  instructions?: string | null
  start_date?: string | null
  page?: number | null
}
export interface ExLab {
  test_name: string
  value?: string | null
  unit?: string | null
  ref_range?: string | null
  flag?: string | null
  date?: string | null
  page?: number | null
}
export interface ExVital {
  kind: string
  value?: string | null
  unit?: string | null
  date?: string | null
  page?: number | null
}
export interface ExEncounter {
  type?: string | null
  date?: string | null
  end_date?: string | null
  reason?: string | null
  diagnosis?: string | null
  notes?: string | null
  advice?: string | null
  follow_up_date?: string | null
  fee?: number | string | null
}
export interface ExtractionResult {
  doc_type?: string | null
  document_date?: string | null
  facility?: string | null
  doctor_name?: string | null
  specialization?: string | null
  summary?: string | null
  conditions?: ExCondition[]
  medications?: ExMedication[]
  lab_results?: ExLab[]
  vitals?: ExVital[]
  encounter?: ExEncounter | null
  confidence?: number | null
  warnings?: string[]
}

export type DocStatus = 'queued' | 'processing' | 'needs_review' | 'committed' | 'failed'

export interface DocumentRow {
  id: number
  filename: string
  mime?: string | null
  kind?: string | null
  size?: number | null
  status: DocStatus
  doc_type?: string | null
  provider?: string | null
  error?: string | null
  uploaded_at?: string | null
  document_date?: string | null
  extraction?: ExtractionResult | null
}

export interface UploadResponse {
  accepted?: DocumentRow[]
  rejected?: { filename: string; reason: string }[]
}

// --- Consent ---
export interface GrantCreated {
  grant_id: number
  otp: string
  otp_expires_at: string
  qr_payload: string | Record<string, unknown>
  dhid?: string
  access_minutes?: number
  scope?: string
}
export interface Grant {
  id: number
  doctor?: { id: number; full_name: string; specialization?: string | null } | null
  doctor_id?: number | null
  doctor_name?: string | null
  doctor_specialization?: string | null
  access_minutes?: number
  otp?: string | null
  created_at?: string
  otp_expires_at?: string | null
  expires_at?: string | null
  scope: 'read' | 'read_write' | string
  revoked_at?: string | null
  status: 'pending' | 'active' | 'expired' | 'revoked' | string
}
export interface RedeemResponse {
  patient_id: number
  expires_at: string
  scope: string
}

export interface DoctorPatient {
  patient_id: number
  full_name: string
  dhid?: string
  age?: number | null
  sex?: string | null
  expires_at?: string | null
  scope?: string
}

export interface Citation {
  document_id: number
  filename?: string | null
  page?: number | null
  snippet?: string | null
}
export interface ToolCall {
  name: string
  args?: Record<string, unknown> | null
  row_count?: number | null
}
export interface AskResponse {
  answer: string
  citations?: Citation[]
  tool_calls?: ToolCall[]
  mode?: 'claude' | 'grok' | 'groq' | 'offline' | string
  warnings?: string[]
}

export interface NotePayload {
  date: string
  reason: string
  diagnosis: string
  notes: string
  advice: string
  follow_up_date: string | null
  fee: number | null
  medications: { drug_name: string; dosage: string; frequency: string; duration: string; instructions: string }[]
}

// --- Admin ---
export interface AuditItem {
  id: number
  ts: string
  actor_id?: number | null
  actor_name?: string | null
  actor_email?: string | null
  actor_role?: string | null
  patient_id?: number | null
  action: string
  detail?: unknown
  ip?: string | null
  hash?: string | null
}
export interface AuditPage {
  items: AuditItem[]
  total: number
}
export interface VerifyResult {
  ok: boolean
  checked?: number
  broken_id?: number | null
}
export interface AdminStats {
  patients?: number
  doctors?: number
  documents?: number
  documents_by_status?: Record<string, number>
  active_grants?: number
  queries_last_7d?: number
}

export interface SystemInfo {
  ai_mode: 'claude' | 'grok' | 'groq' | 'offline' | string
  model?: string | null
  ocr_available?: boolean
  ffmpeg_available?: boolean
  max_upload_mb?: number
}
