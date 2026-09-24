export type MasterKind = "items" | "vendors" | "ledgers";
export type MasterSuggestion = { id: string; name: string; detail: string };

export async function fetchMasterSuggestions(
  kind: MasterKind, query: string, signal?: AbortSignal,
): Promise<MasterSuggestion[]> {
  const params = new URLSearchParams({ kind, q: query.slice(0, 200) });
  const res = await apiFetch(`/api/tally/masters/suggestions?${params}`, { signal });
  if (!res.ok) throw new Error("Suggestions unavailable. You can still enter a name.");
  return res.json();
}

export type InvoiceSummary = {
  id: string;
  file_path: string | null;
  uploaded_file_path: string | null;
  source_files?: string[] | null;
  file_status?: string | null;
  invoice_number: unknown;
  total_amount: unknown;
  hsn_value: unknown;
  invoice_date?: string | null;
  seller?: string | null;
  service_category?: string | null;
  line_item_index?: number | null;
  status?: number | null;
  payment_status?: string | null;
  quantity?: unknown | null;
  unit?: unknown | null;
  price_per_unit?: unknown | null;
  amount?: unknown | null;
  tax_rate?: unknown | null;
  tax_amount?: unknown | null;
  amount_after_tax?: unknown | null;
  sub_total?: unknown | null;
  sgst_rate?: unknown | null;
  sgst_amount?: unknown | null;
  cgst_rate?: unknown | null;
  cgst_amount?: unknown | null;
  igst_rate?: unknown | null;
  igst_amount?: unknown | null;
  discount?: unknown | null;
  round_off?: unknown | null;
  summary_total_amount?: unknown | null;
  hitl?: boolean | null;
  hitl_remark?: string | null;
  hitl_remarks?: string[] | null;
  deblurred_applied?: boolean | null;
  human_approved?: boolean | null;
  // ERP / Tally master-data fuzzy-match results — see backend/erp_matching.py.
  po_id?: string | null;
  vendor_id?: string | null;
  vendor_match_score?: number | null;
  vendor_match_name?: string | null;
  po_match_score?: number | null;
  po_business_unit?: string | null;
  item_id?: string | null;
  item_match_score?: number | null;
  line_match_type?: "STOCK_ITEM" | "LEDGER" | "UNMATCHED" | null;
  ledger_id?: string | null;
  ledger_match_score?: number | null;
  erp_ledger_name?: string | null;
  original_name?: string | null;
  original_quantity?: unknown | null;
  original_rate?: unknown | null;
  original_amount?: unknown | null;
  matched_name?: string | null;
  match_type?: "STOCK_ITEM" | "LEDGER" | "UNMATCHED" | null;
  match_score?: number | null;
  tally_master_id?: string | null;
  matching_status?: "MATCHED" | "HITL_REQUIRED" | null;
  /** True when ERP matching is current and left no vendor/item/PO gaps. */
  erp_matching_complete?: boolean;
  /** Tally push outcome shown in ERP-Remark column. */
  erp_remark?: string | null;
  tally_push_status?: string | null;
  tally_error_reason?: string | null;
};

export type InvoiceListResponse = {
  items: InvoiceSummary[];
  total_amount_sum: number;
};

export type InvoiceFilters = {
  start_date?: string;
  end_date?: string;
  status?: string;
  payment_status?: string;
  file_status?: string;
};

export type DeleteInvoicesResponse = {
  requested_count: number;
  deleted_count: number;
};

export type ChatMessage = {
  role: "user" | "assistant" | string;
  content: string;
  timestamp?: string | null;
};

export type ChatResponse = {
  answer: string;
  session_id: string;
  messages: ChatMessage[];
  suggestions: string[];
  mode?: string | null;
};

export type LicenseProfile = {
  plan: "monthly" | "yearly" | "quota" | "onetime" | "dev" | string;
  planLabel: string;
  customerId: string;
  issuedAt: string | null;
  expiresAt: string | null;
  remainingDays: number | null;
  invoiceLimit: number | null;
  invoicesUsed: number | null;
  invoicesRemaining: number | null;
  isUnlimited: boolean;
  statusMessage: string;
};

export type PipelineStatus = {
  generated_at: string;
  awaiting_processing: number;
  in_process: number;
  processed: number;
  error: number;
  gemini_api_error?: number;
  hitl_pending: number;
  queue_total: number;
  api_staging: number;
  watcher_active: boolean;
  async_jobs: number;
  stored_total: number;
  stored_healthy: number;
  stored_errors: number;
  hitl_flagged_total: number;
  hitl_review_pending: number;
  hitl_reviewed: number;
  system_processed: number;
  human_approved_files: number;
  gemini_quota_error_count?: number;
  network_error_count?: number;
  erp_configured?: boolean;
  erp_matched_files?: number;
  erp_pending_files?: number;
};

export type InvoiceJsonEditorResponse = {
  id: string;
  uploaded_file_path?: string | null;
  source_files?: string[] | null;
  gemini_json: Record<string, unknown>;
  line_items: Record<string, unknown>[];
};

export type SearchField = "invoice_number" | "hsn_value" | "seller" | "service_category";

const JSON_HEADERS = {
  "Content-Type": "application/json",
};

async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return fetch(input, { ...init, credentials: "include" });
}

export type AuthStatus = {
  authenticated: boolean;
  username?: string | null;
};

export async function fetchHealth(): Promise<boolean> {
  try {
    const res = await apiFetch("/api/health");
    if (!res.ok) {
      return false;
    }
    const data = (await res.json()) as { status?: string };
    return data.status === "ok";
  } catch {
    return false;
  }
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const res = await apiFetch("/api/auth/me");
  if (!res.ok) {
    throw new Error(`Failed to check auth status (${res.status})`);
  }
  return res.json();
}

export async function loginDashboard(username: string, password: string): Promise<AuthStatus> {
  const res = await apiFetch("/api/auth/login", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = await res.text();
    try {
      const parsed = JSON.parse(detail) as { detail?: string };
      if (parsed.detail) {
        detail = parsed.detail;
      }
    } catch {
      // Use raw response text.
    }
    throw new Error(detail || `Login failed (${res.status})`);
  }
  return res.json();
}

export async function logoutDashboard(): Promise<void> {
  const res = await apiFetch("/api/auth/logout", { method: "POST" });
  if (!res.ok) {
    throw new Error(`Logout failed (${res.status})`);
  }
}

export async function fetchInvoices(
  filters: InvoiceFilters = {},
): Promise<InvoiceListResponse> {
  const params = new URLSearchParams();
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  if (filters.status) params.set("status", filters.status);
  if (filters.payment_status) params.set("payment_status", filters.payment_status);
  if (filters.file_status) params.set("file_status", filters.file_status);

  const query = params.toString();
  const url = query ? `/api/invoices?${query}` : "/api/invoices";

  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`Failed to load invoices (${res.status})`);
  }
  return res.json();
}

export async function uploadInvoice(file: File): Promise<InvoiceSummary> {
  const form = new FormData();
  form.append("file", file);

  const res = await apiFetch("/api/upload", {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Upload failed (${res.status})`);
  }
  return res.json();
}

export type InvoiceUpdatePayload = Partial<{
  invoice_number: string | null;
  total_amount: string | null;
  hsn_value: string | null;
  invoice_date: string | null;
  seller: string | null;
  service_category: string | null;
  line_item_index: number | null;
  status: string | null;
  payment_status: string | null;
  quantity: string | null;
  unit: string | null;
  price_per_unit: string | null;
  amount: string | null;
  tax_rate: string | null;
  tax_amount: string | null;
  amount_after_tax: string | null;
  sub_total: string | null;
  sgst_rate: string | null;
  sgst_amount: string | null;
  cgst_rate: string | null;
  cgst_amount: string | null;
  igst_rate: string | null;
  igst_amount: string | null;
  discount: string | null;
  round_off: string | null;
}>;

export async function updateInvoice(
  id: string,
  payload: InvoiceUpdatePayload,
): Promise<InvoiceSummary> {
  const res = await apiFetch(`/api/invoices/${id}`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Update failed (${res.status})`);
  }
  return res.json();
}

export async function humanApproveInvoice(
  id: string,
): Promise<InvoiceSummary> {
  const res = await apiFetch(`/api/invoices/${id}/human-approve`, {
    method: "POST",
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Human approval failed (${res.status})`);
  }
  return res.json();
}

export async function addInvoiceLineItem(
  id: string,
  payload: Partial<{
    hsn_number: string;
    service: string;
    quantity: string;
    unit: string;
    price_per_unit: string;
    amount: string;
    tax_rate: string;
    tax_amount: string;
    amount_after_tax: string;
  }> = {},
): Promise<InvoiceSummary> {
  const res = await apiFetch(`/api/invoices/${id}/line-items`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Add line item failed (${res.status})`);
  }
  return res.json();
}

export async function deleteInvoiceLineItem(
  invoiceId: string,
  lineItemIndex: number,
): Promise<void> {
  const res = await apiFetch(`/api/invoices/${invoiceId}/line-items/${lineItemIndex}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Delete line item failed (${res.status})`);
  }
}

export async function deleteInvoices(
  ids: string[],
): Promise<DeleteInvoicesResponse> {
  if (ids.length === 0) {
    return { requested_count: 0, deleted_count: 0 };
  }

  const res = await apiFetch("/api/invoices/delete", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Delete failed (${res.status})`);
  }
  return (await res.json()) as DeleteInvoicesResponse;
}

export async function fetchLicenseProfile(): Promise<LicenseProfile> {
  const res = await apiFetch("/api/license");
  if (!res.ok) {
    throw new Error(`Failed to load license profile (${res.status})`);
  }
  return res.json();
}

export async function fetchPdfPageCount(filename: string): Promise<number> {
  const res = await apiFetch(`/api/raw-pdf-info/${encodeURIComponent(filename)}`);
  if (!res.ok) {
    throw new Error(`Failed to load PDF info (${res.status})`);
  }
  const data = (await res.json()) as { page_count?: number };
  return typeof data.page_count === "number" && data.page_count > 0 ? data.page_count : 1;
}

export function pdfPageImageUrl(filename: string, page: number): string {
  return `/api/raw-pdf-page/${encodeURIComponent(filename)}?page=${page}`;
}

export type AgentSettings = {
  model: string | null;
  api_key_masked: string | null;
  configured: boolean;
  supported_models: string[];
};

export type ConfigStatus = {
  configured: boolean;
  missing: string[];
  message: string | null;
};

export type HealthItem = {
  id: string;
  label: string;
  status: "ok" | "warning" | "error" | "info" | "disabled";
  message: string;
  fix_route?: string | null;
  fix_hint?: string | null;
};

export type HealthSection = {
  id: string;
  title: string;
  status: HealthItem["status"];
  items: HealthItem[];
};

export type HealthLogIssue = {
  time: string;
  level: string;
  message: string;
};

export type SystemHealth = {
  overall: "ok" | "warning" | "error";
  checked_at: string;
  summary: { ok: number; warning: number; error: number };
  sections: HealthSection[];
  recent_issues: HealthLogIssue[];
  critical_messages: string[];
};

export async function fetchSystemHealth(): Promise<SystemHealth> {
  const res = await apiFetch("/api/system-health");
  if (!res.ok) {
    throw new Error(`Failed to load system health (${res.status})`);
  }
  return res.json();
}

export async function fetchAgentSettings(): Promise<AgentSettings> {
  const res = await apiFetch("/api/agent-settings");
  if (!res.ok) {
    throw new Error(`Failed to load AI agent settings (${res.status})`);
  }
  return res.json();
}

export async function saveAgentSettings(
  model: string,
  apiKey: string,
): Promise<AgentSettings> {
  const res = await apiFetch("/api/agent-settings", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ model, api_key: apiKey }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to save AI agent settings (${res.status})`);
  }
  return res.json();
}

export async function fetchConfigStatus(): Promise<ConfigStatus> {
  const res = await apiFetch("/api/config-status");
  if (!res.ok) {
    throw new Error(`Failed to load configuration status (${res.status})`);
  }
  return res.json();
}

export async function fetchPipelineStatus(): Promise<PipelineStatus> {
  const res = await apiFetch("/api/telemetry/pipeline-status");
  if (!res.ok) {
    throw new Error(`Failed to load pipeline status (${res.status})`);
  }
  return res.json();
}

export type ErpSyncMode = "immediate" | "scheduled";

export type ErpSyncResult = {
  scanned: number;
  updated: number;
  errored: number;
  merged_groups?: number;
  merged_docs?: number;
};

export type TallySyncResult = {
  scanned: number;
  pushed: number;
  skipped: number;
  errored: number;
};

export type ErpSyncSettings = {
  mode: ErpSyncMode;
  frequency_minutes: number;
  merge_hitl_duplicates: boolean;
  last_synced_at: string | null;
  last_sync_result: ErpSyncResult | null;
  syncing: boolean;
  configured: boolean;
  // Computed server-side from whichever is more recent of the last completed sync or
  // the last settings save - see backend/erp_settings.py: next_sync_baseline().
  next_sync_at: string | null;
  tally_configured?: boolean;
  last_tally_synced_at?: string | null;
  last_tally_sync_result?: TallySyncResult | null;
};

export type TallyPushResponse = {
  success: boolean;
  invoice_id: string;
  invoice_number?: string | null;
  erp_remark: string;
  error_reason?: string | null;
  tally_company?: string | null;
  pushed_at?: string | null;
  skipped?: boolean;
};

export async function pushInvoiceToTally(
  invoiceId: string,
  force = false,
): Promise<TallyPushResponse> {
  const qs = force ? "?force=true" : "";
  const res = await apiFetch(`/api/tally/push/${encodeURIComponent(invoiceId)}${qs}`, {
    method: "POST",
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to push invoice to Tally (${res.status})`);
  }
  return res.json();
}

export async function fetchErpSyncSettings(): Promise<ErpSyncSettings> {
  const res = await apiFetch("/api/erp/settings");
  if (!res.ok) {
    throw new Error(`Failed to load ERP sync settings (${res.status})`);
  }
  return res.json();
}

export async function forceErpSync(): Promise<ErpSyncSettings> {
  const res = await apiFetch("/api/erp/sync", { method: "POST" });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to start ERP sync (${res.status})`);
  }
  return res.json();
}

export async function saveErpSyncSettings(payload: {
  mode: ErpSyncMode;
  frequency_minutes: number;
  merge_hitl_duplicates?: boolean;
}): Promise<ErpSyncSettings> {
  const res = await apiFetch("/api/erp/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to save ERP sync settings (${res.status})`);
  }
  return res.json();
}

export type TallyPurchaseLedgers = {
  ledgers: string[];
  company?: string | null;
  error?: string | null;
  tally_configured?: boolean;
  tally_reachable?: boolean;
};

export type TallyLedgerSettings = {
  purchase_ledger: string;
  updated_at?: string | null;
  tally_configured?: boolean;
};

export type TallyCompanySettings = {
  company_name: string;
  tally_host: string;
  tally_port: number;
};

export async function fetchTallyCompanySettings(): Promise<TallyCompanySettings> {
  const res = await apiFetch("/api/tally/company-settings");
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to load company details (${res.status})`);
  }
  return res.json();
}

export type TallyCompanySettingsPayload = {
  company_name: string;
  tally_host: string;
  tally_port: number;
};

export async function saveTallyCompanySettings(
  payload: TallyCompanySettingsPayload,
): Promise<TallyCompanySettings> {
  const res = await apiFetch("/api/tally/company-settings", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let msg = await res.text();
    try {
      msg = (JSON.parse(msg) as { detail?: string }).detail || msg;
    } catch {
      // Keep the raw response.
    }
    throw new Error(msg || `Failed to save company details (${res.status})`);
  }
  return res.json();
}

export async function fetchTallyPurchaseLedgers(): Promise<TallyPurchaseLedgers> {
  const res = await apiFetch("/api/tally/purchase-ledgers");
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to load purchase ledgers (${res.status})`);
  }
  return res.json();
}

export async function fetchTallyLedgerSettings(): Promise<TallyLedgerSettings> {
  const res = await apiFetch("/api/tally/ledger-settings");
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to load ledger settings (${res.status})`);
  }
  return res.json();
}

export async function saveTallyLedgerSettings(
  purchaseLedger: string,
): Promise<TallyLedgerSettings> {
  const res = await apiFetch("/api/tally/ledger-settings", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ purchase_ledger: purchaseLedger }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to save ledger settings (${res.status})`);
  }
  return res.json();
}

export type TallyMasterSyncResult = {
  vendors: number;
  items: number;
  expense_ledgers: number;
  po_headers: number;
  po_lines: number;
  errors: string[];
  success: boolean;
};

export type TallyMasterSyncStatus = {
  tally_configured: boolean;
  configured: boolean;
  syncing: boolean;
  company?: string | null;
  last_synced_at?: string | null;
  last_result?: TallyMasterSyncResult | null;
  last_error?: string | null;
  counts: {
    vendors?: number;
    items?: number;
    expense_ledgers?: number;
    po_headers?: number;
    po_lines?: number;
  };
};

export type TallyMasterSchedulerMode = "manual" | "scheduled" | "time_based";

export type TallyMasterSchedulerSettings = {
  mode: TallyMasterSchedulerMode;
  frequency_minutes: number;
  rematch_after_scheduled_refresh: boolean;
  scheduled_times: string[];
  timezone: string;
  next_refresh_at: string | null;
  tally_configured: boolean;
};

export type TallyMasterRefreshResponse = {
  started: boolean;
  message: string;
  status?: TallyMasterSyncStatus;
};

export async function fetchTallyMasterStatus(): Promise<TallyMasterSyncStatus> {
  const res = await apiFetch("/api/tally/masters/status");
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to load Tally master status (${res.status})`);
  }
  return res.json();
}

export async function refreshTallyMasterData(
  rematch = true,
): Promise<TallyMasterRefreshResponse> {
  const params = new URLSearchParams({
    rematch: rematch ? "true" : "false",
    async: "true",
  });
  const res = await apiFetch(`/api/tally/masters/refresh?${params.toString()}`, {
    method: "POST",
  });
  if (!res.ok) {
    let msg = await res.text();
    try {
      const body = JSON.parse(msg);
      msg = body.detail || msg;
    } catch {
      /* keep raw text */
    }
    throw new Error(msg || `Failed to refresh Tally master data (${res.status})`);
  }
  return res.json();
}

export async function fetchTallyMasterSchedulerSettings(): Promise<TallyMasterSchedulerSettings> {
  const res = await apiFetch("/api/tally/masters/settings");
  if (!res.ok) {
    throw new Error(`Failed to load Tally scheduler settings (${res.status})`);
  }
  return res.json();
}

export async function saveTallyMasterSchedulerSettings(payload: {
  mode: TallyMasterSchedulerMode;
  frequency_minutes?: number;
  rematch_after_scheduled_refresh: boolean;
  scheduled_times?: string[];
  timezone?: string;
}): Promise<TallyMasterSchedulerSettings> {
  const res = await apiFetch("/api/tally/masters/settings", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to save Tally scheduler settings (${res.status})`);
  }
  return res.json();
}

export type HitlNotificationTriggerMode =
  | "immediate"
  | "scheduled_digest"
  | "threshold_only";

export type HitlNotificationSettings = {
  enabled: boolean;
  recipient_emails: string[];
  trigger_mode: HitlNotificationTriggerMode;
  digest_frequency_minutes: number;
  pending_threshold: number;
  last_sent_at: string | null;
  last_pending_count: number;
  smtp_configured: boolean;
  next_digest_at: string | null;
  hitl_pending_count: number;
};

export async function fetchHitlNotificationSettings(): Promise<HitlNotificationSettings> {
  const res = await apiFetch("/api/notifications/hitl-settings");
  if (!res.ok) {
    throw new Error(`Failed to load HITL notification settings (${res.status})`);
  }
  return res.json();
}

export async function saveHitlNotificationSettings(payload: {
  enabled: boolean;
  recipient_emails: string[];
  trigger_mode: HitlNotificationTriggerMode;
  digest_frequency_minutes: number;
  pending_threshold: number;
}): Promise<HitlNotificationSettings> {
  const res = await apiFetch("/api/notifications/hitl-settings", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to save HITL notification settings (${res.status})`);
  }
  return res.json();
}

export async function sendHitlNotificationTest(): Promise<{ success: boolean; message: string }> {
  const res = await apiFetch("/api/notifications/hitl-settings/test", { method: "POST" });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to send test email (${res.status})`);
  }
  return res.json();
}

const CHAT_SESSION_KEY = "idp_chat_session_id";

export function getChatSessionId(): string | null {
  try {
    return sessionStorage.getItem(CHAT_SESSION_KEY);
  } catch {
    return null;
  }
}

export function setChatSessionId(sessionId: string): void {
  try {
    sessionStorage.setItem(CHAT_SESSION_KEY, sessionId);
  } catch {
    /* ignore */
  }
}

export async function fetchChatSuggestions(): Promise<string[]> {
  const res = await apiFetch("/api/chat/suggestions");
  if (!res.ok) {
    throw new Error(`Failed to load chat suggestions (${res.status})`);
  }
  const data = (await res.json()) as { suggestions?: string[] };
  return Array.isArray(data.suggestions) ? data.suggestions : [];
}

export async function fetchChatSession(sessionId: string): Promise<{
  session_id: string;
  messages: ChatMessage[];
}> {
  const res = await apiFetch(`/api/chat/session/${encodeURIComponent(sessionId)}`);
  if (!res.ok) {
    throw new Error(`Failed to load chat session (${res.status})`);
  }
  return res.json();
}

export async function chat(
  question: string,
  sessionId?: string | null,
): Promise<ChatResponse> {
  const res = await apiFetch("/api/chat", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      question,
      session_id: sessionId ?? getChatSessionId(),
    }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Chat failed (${res.status})`);
  }
  const data = (await res.json()) as ChatResponse;
  if (data.session_id) {
    setChatSessionId(data.session_id);
  }
  return data;
}

export async function fetchSearchValues(field: SearchField): Promise<string[]> {
  const params = new URLSearchParams({ field });
  const res = await apiFetch(`/api/invoices/search/values?${params.toString()}`);
  if (!res.ok) {
    throw new Error(`Failed to load search values (${res.status})`);
  }
  const data = (await res.json()) as { values?: string[] };
  return Array.isArray(data.values) ? data.values : [];
}

export async function fetchSearchSuggestions(
  field: SearchField,
  query: string,
): Promise<string[]> {
  const params = new URLSearchParams({ field, q: query });
  const res = await apiFetch(`/api/invoices/search/suggestions?${params.toString()}`);
  if (!res.ok) {
    throw new Error(`Failed to load search suggestions (${res.status})`);
  }
  const data = (await res.json()) as { suggestions?: string[] };
  return Array.isArray(data.suggestions) ? data.suggestions : [];
}

export async function fetchInvoiceJsonEditor(
  id: string,
): Promise<InvoiceJsonEditorResponse> {
  const res = await apiFetch(`/api/invoices/${id}/json-editor`);
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to load invoice editor data (${res.status})`);
  }
  return (await res.json()) as InvoiceJsonEditorResponse;
}

/** ERP page download only — blocked until ERP matching is complete for this invoice. */
export async function fetchInvoiceErpExport(
  id: string,
): Promise<InvoiceJsonEditorResponse> {
  const res = await apiFetch(`/api/invoices/${id}/erp-export`);
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to export invoice JSON (${res.status})`);
  }
  return (await res.json()) as InvoiceJsonEditorResponse;
}

export async function saveInvoiceJsonEditor(
  id: string,
  payload: {
    gemini_json: Record<string, unknown>;
    line_items: Record<string, unknown>[];
  },
): Promise<InvoiceSummary> {
  const res = await apiFetch(`/api/invoices/${id}/json-editor`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to save invoice editor data (${res.status})`);
  }
  return (await res.json()) as InvoiceSummary;
}
