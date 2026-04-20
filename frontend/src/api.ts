export type InvoiceSummary = {
  id: string;
  file_path: string | null;
  uploaded_file_path: string | null;
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
  summary_total_amount?: unknown | null;
  hitl?: boolean | null;
  deblurred_applied?: boolean | null;
  human_approved?: boolean | null;
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

export type ChatResponse = {
  answer: string;
};

export type InvoiceJsonEditorResponse = {
  id: string;
  uploaded_file_path?: string | null;
  gemini_json: Record<string, unknown>;
  line_items: Record<string, unknown>[];
};

export type SearchField = "invoice_number" | "hsn_value" | "seller" | "service_category";

const JSON_HEADERS = {
  "Content-Type": "application/json",
};

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

  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to load invoices (${res.status})`);
  }
  return res.json();
}

export async function uploadInvoice(file: File): Promise<InvoiceSummary> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch("/api/upload", {
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
}>;

export async function updateInvoice(
  id: string,
  payload: InvoiceUpdatePayload,
): Promise<InvoiceSummary> {
  const res = await fetch(`/api/invoices/${id}`, {
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
  const res = await fetch(`/api/invoices/${id}/human-approve`, {
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
    price_per_unit: string;
    amount: string;
    tax_rate: string;
    tax_amount: string;
    amount_after_tax: string;
  }> = {},
): Promise<InvoiceSummary> {
  const res = await fetch(`/api/invoices/${id}/line-items`, {
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
  const res = await fetch(`/api/invoices/${invoiceId}/line-items/${lineItemIndex}`, {
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

  const res = await fetch("/api/invoices/delete", {
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

export async function chat(question: string): Promise<ChatResponse> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Chat failed (${res.status})`);
  }
  return res.json();
}

export async function fetchSearchValues(field: SearchField): Promise<string[]> {
  const params = new URLSearchParams({ field });
  const res = await fetch(`/api/invoices/search/values?${params.toString()}`);
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
  const res = await fetch(`/api/invoices/search/suggestions?${params.toString()}`);
  if (!res.ok) {
    throw new Error(`Failed to load search suggestions (${res.status})`);
  }
  const data = (await res.json()) as { suggestions?: string[] };
  return Array.isArray(data.suggestions) ? data.suggestions : [];
}

export async function fetchInvoiceJsonEditor(
  id: string,
): Promise<InvoiceJsonEditorResponse> {
  const res = await fetch(`/api/invoices/${id}/json-editor`);
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || `Failed to load invoice editor data (${res.status})`);
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
  const res = await fetch(`/api/invoices/${id}/json-editor`, {
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

