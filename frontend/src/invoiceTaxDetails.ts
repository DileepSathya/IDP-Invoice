export type InvoiceTaxDetails = {
  cgst_rate: string;
  cgst_amount: string;
  sgst_rate: string;
  sgst_amount: string;
  igst_rate: string;
  igst_amount: string;
  discount: string;
  round_off: string;
};

export type InvoiceAdditionalFieldRow = {
  key: string;
  value: string;
  originalValue?: unknown;
  originalText?: string;
};

export const EMPTY_INVOICE_TAX_DETAILS: InvoiceTaxDetails = {
  cgst_rate: "",
  cgst_amount: "",
  sgst_rate: "",
  sgst_amount: "",
  igst_rate: "",
  igst_amount: "",
  discount: "",
  round_off: "",
};

const TAX_DETAIL_ALIASES: Record<keyof InvoiceTaxDetails, readonly string[]> = {
  cgst_rate: ["cgst_rate"],
  cgst_amount: ["cgst_amount"],
  sgst_rate: ["sgst_rate"],
  sgst_amount: ["sgst_amount"],
  igst_rate: ["igst_rate"],
  igst_amount: ["igst_amount", "total_igst_amount"],
  discount: ["discount", "discount_amount", "total_discount"],
  round_off: ["round_off", "roundoff", "round_off_amount", "square_off", "square_off_amount", "rounding"],
};

const TAX_DETAIL_KEYS = new Set(Object.values(TAX_DETAIL_ALIASES).flat());
const PRESERVED_EDITOR_METADATA_KEYS = [
  "HITL",
  "status",
  "ever_hitl_true",
  "deblurred_applied",
  "human_approved",
  "hitl_remark",
  "hitl_remarks",
] as const;

function toText(value: unknown): string {
  return value == null ? "" : String(value);
}

export function normalizeHitlRemarks(
  hitlRemark: unknown,
  hitlRemarks: unknown,
): string[] {
  if (Array.isArray(hitlRemarks)) {
    const reasons = hitlRemarks.map(toText).map((reason) => reason.trim()).filter(Boolean);
    if (reasons.length > 0) return reasons;
  }

  return toText(hitlRemark)
    .split(";")
    .map((reason) => reason.trim())
    .filter(Boolean);
}

function readCanonicalValue(fields: Record<string, unknown>, aliases: readonly string[]): string {
  for (const key of aliases) {
    if (Object.prototype.hasOwnProperty.call(fields, key)) {
      const value = toText(fields[key]);
      if (value.trim()) return value;
    }
  }
  return "";
}

export function normalizeInvoiceTaxDetails(fields: Record<string, unknown>): {
  taxDetails: InvoiceTaxDetails;
  remainingFields: Record<string, unknown>;
} {
  const remainingFields = Object.fromEntries(
    Object.entries(fields).filter(([key]) => !TAX_DETAIL_KEYS.has(key)),
  );

  return {
    taxDetails: {
      cgst_rate: readCanonicalValue(fields, TAX_DETAIL_ALIASES.cgst_rate),
      cgst_amount: readCanonicalValue(fields, TAX_DETAIL_ALIASES.cgst_amount),
      sgst_rate: readCanonicalValue(fields, TAX_DETAIL_ALIASES.sgst_rate),
      sgst_amount: readCanonicalValue(fields, TAX_DETAIL_ALIASES.sgst_amount),
      igst_rate: readCanonicalValue(fields, TAX_DETAIL_ALIASES.igst_rate),
      igst_amount: readCanonicalValue(fields, TAX_DETAIL_ALIASES.igst_amount),
      discount: readCanonicalValue(fields, TAX_DETAIL_ALIASES.discount),
      round_off: readCanonicalValue(fields, TAX_DETAIL_ALIASES.round_off),
    },
    remainingFields,
  };
}

export function serializeInvoiceTaxDetails(
  fields: Record<string, unknown>,
  taxDetails: InvoiceTaxDetails,
): Record<string, unknown> {
  const { remainingFields } = normalizeInvoiceTaxDetails(fields);
  return { ...remainingFields, ...taxDetails };
}

export function invoiceTaxDetailsToRows(taxDetails: InvoiceTaxDetails): Array<{ key: string; value: string }> {
  return Object.entries(taxDetails).map(([key, value]) => ({ key, value }));
}

export function serializeAdditionalFieldRows(rows: InvoiceAdditionalFieldRow[]): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    const isUnchanged = row.originalText !== undefined && row.value === row.originalText;
    result[key] = isUnchanged ? row.originalValue : row.value;
  }
  return result;
}

export function preserveInvoiceEditorMetadata(
  target: Record<string, unknown>,
  previous: Record<string, unknown>,
): Record<string, unknown> {
  const result = { ...target };
  for (const key of PRESERVED_EDITOR_METADATA_KEYS) {
    if (Object.prototype.hasOwnProperty.call(previous, key)) {
      result[key] = previous[key];
    }
  }
  return result;
}
