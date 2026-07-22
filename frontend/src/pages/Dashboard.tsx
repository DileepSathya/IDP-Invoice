import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  InvoiceSummary,
  InvoiceListResponse,
  SearchField,
  LicenseProfile,
  ConfigStatus,
  fetchConfigStatus,
  fetchLicenseProfile,
  fetchPipelineStatus,
  fetchSearchSuggestions,
  fetchSearchValues,
  fetchInvoices,
  fetchInvoiceJsonEditor,
  fetchPdfPageCount,
  pdfPageImageUrl,
  uploadInvoice,
  updateInvoice,
  saveInvoiceJsonEditor,
  addInvoiceLineItem,
  deleteInvoiceLineItem,
  deleteInvoices,
} from "../api";
import { PlanBanner } from "../components/PlanBanner";

type InvoiceEditorFormState = {
  invoice_number: string;
  invoice_date: string;
  due_date: string;
  seller: string;
  buyer: string;
  address: string;
  bank_name: string;
  bank_address: string;
  account_number: string;
  account_holder_name: string;
  ifsc_code: string;
  total_amount: string;
  po_id: string;
  term_to_pay: string;
};

type InvoiceEditorLineItem = {
  hsn_number: string;
  service: string;
  quantity: string;
  unit: string;
  price_per_unit: string;
  amount: string;
  tax_rate: string;
  tax_amount: string;
  amount_after_tax: string;
};

type InvoiceEditorAdditionalField = {
  key: string;
  value: string;
};

// Shared across the editing-state, EditableCell props, and the change/commit
// handlers below so adding a new editable billing-summary field (e.g. IGST,
// discount, round-off) only means updating this one list instead of the
// half-dozen near-identical inline unions that used to be repeated per site.
type EditableInvoiceField =
  | "invoice_number"
  | "total_amount"
  | "hsn_value"
  | "invoice_date"
  | "seller"
  | "service_category"
  | "quantity"
  | "unit"
  | "price_per_unit"
  | "amount"
  | "tax_rate"
  | "tax_amount"
  | "amount_after_tax"
  | "sub_total"
  | "sgst_rate"
  | "sgst_amount"
  | "cgst_rate"
  | "cgst_amount"
  | "igst_rate"
  | "igst_amount"
  | "discount"
  | "round_off";

export const Dashboard: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [allInvoices, setAllInvoices] = useState<InvoiceSummary[]>([]);
  const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [fileStatusFilter, setFileStatusFilter] = useState<"" | "error" | "healthy file">("");
  const [selectForDelete, setSelectForDelete] = useState(false);
  const [selectedInvoiceIds, setSelectedInvoiceIds] = useState<string[]>([]);
  const [expandedInvoiceIds, setExpandedInvoiceIds] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [searchField, setSearchField] = useState<SearchField | "">("");
  const [searchValue, setSearchValue] = useState("");
  const [searchOptions, setSearchOptions] = useState<string[]>([]);
  const [loadingSearchOptions, setLoadingSearchOptions] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewZoom, setPreviewZoom] = useState<number>(1);
  const [previewPdfPage, setPreviewPdfPage] = useState(1);
  const [previewPdfPageCount, setPreviewPdfPageCount] = useState(1);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [previewPosition, setPreviewPosition] = useState<{ top: number; left: number }>({
    top: 100,
    left: 100,
  });
  const [isDraggingPreview, setIsDraggingPreview] = useState(false);
  const [isPanning, setIsPanning] = useState(false);
  const [showHitlOnly, setShowHitlOnly] = useState(false);
  const [jsonEditorOpen, setJsonEditorOpen] = useState(false);
  const [jsonEditorInvoiceId, setJsonEditorInvoiceId] = useState<string | null>(null);
  const [jsonEditorBase, setJsonEditorBase] = useState<Record<string, unknown>>({});
  const [jsonEditorForm, setJsonEditorForm] = useState<InvoiceEditorFormState>({
    invoice_number: "",
    invoice_date: "",
    due_date: "",
    seller: "",
    buyer: "",
    address: "",
    bank_name: "",
    bank_address: "",
    account_number: "",
    account_holder_name: "",
    ifsc_code: "",
    total_amount: "",
    po_id: "",
    term_to_pay: "",
  });
  const [jsonEditorLineItems, setJsonEditorLineItems] = useState<InvoiceEditorLineItem[]>([]);
  const [jsonEditorAdditionalFields, setJsonEditorAdditionalFields] = useState<InvoiceEditorAdditionalField[]>([]);
  const [jsonEditorLoading, setJsonEditorLoading] = useState(false);
  const [jsonEditorSaving, setJsonEditorSaving] = useState(false);
  const [saveConfirmOpen, setSaveConfirmOpen] = useState(false);
  const [saveComment, setSaveComment] = useState("");
  const [licenseProfile, setLicenseProfile] = useState<LicenseProfile | null>(null);
  const [configStatus, setConfigStatus] = useState<ConfigStatus | null>(null);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null);
  const pipelineSnapshotRef = useRef<string | null>(null);
  const [jsonEditorPreviewUrl, setJsonEditorPreviewUrl] = useState<string | null>(null);
  const [jsonEditorPreviewZoom, setJsonEditorPreviewZoom] = useState(1);
  const [jsonEditorPreviewRotate, setJsonEditorPreviewRotate] = useState(0);
  const [jsonEditorPdfPage, setJsonEditorPdfPage] = useState(1);
  const [jsonEditorPdfPageCount, setJsonEditorPdfPageCount] = useState(1);
  const [editing, setEditing] = useState<{
    id: string;
    field: EditableInvoiceField;
    lineItemIndex: number | null | undefined;
  } | null>(null);
  const panStart = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const previewWrapperRef = useRef<HTMLDivElement | null>(null);
  const previewDragStart = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const handleSelectPreview = (url: string, target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    const panelWidth = 560;
    const panelHeight = 620;
    const viewportPadding = 12;
    const preferredGap = 12;
    const canOpenRight = rect.right + preferredGap + panelWidth <= window.innerWidth - viewportPadding;

    let left = canOpenRight ? rect.right + preferredGap : rect.left - panelWidth - preferredGap;
    let top = rect.top;

    if (left < viewportPadding) left = viewportPadding;
    if (left + panelWidth > window.innerWidth - viewportPadding) {
      left = window.innerWidth - panelWidth - viewportPadding;
    }
    if (top < viewportPadding) top = viewportPadding;
    if (top + panelHeight > window.innerHeight - viewportPadding) {
      top = Math.max(viewportPadding, window.innerHeight - panelHeight - viewportPadding);
    }

    setPreviewPosition({ top, left });
    setPreviewUrl(url);
    setPreviewZoom(1);
    setIsPreviewOpen(true);
  };

  const handlePreviewMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!previewWrapperRef.current) return;
    setIsPanning(true);
    panStart.current = {
      x: e.clientX,
      y: e.clientY,
      left: previewWrapperRef.current.scrollLeft,
      top: previewWrapperRef.current.scrollTop,
    };
  };

  const handlePreviewMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!isPanning || !previewWrapperRef.current || !panStart.current) return;
    const dx = e.clientX - panStart.current.x;
    const dy = e.clientY - panStart.current.y;
    previewWrapperRef.current.scrollLeft = panStart.current.left - dx;
    previewWrapperRef.current.scrollTop = panStart.current.top - dy;
  };

  const handlePreviewMouseUp = () => {
    setIsPanning(false);
  };

  const handlePreviewHeaderMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return;
    e.preventDefault();
    previewDragStart.current = {
      x: e.clientX,
      y: e.clientY,
      left: previewPosition.left,
      top: previewPosition.top,
    };
    setIsDraggingPreview(true);
  };

  useEffect(() => {
    if (!isDraggingPreview) return;
    const viewportPadding = 12;
    const panelWidth = Math.min(560, window.innerWidth - viewportPadding * 2);
    const panelHeight = Math.min(620, window.innerHeight - viewportPadding * 2);

    const handleMove = (e: MouseEvent) => {
      if (!previewDragStart.current) return;
      const dx = e.clientX - previewDragStart.current.x;
      const dy = e.clientY - previewDragStart.current.y;
      const maxLeft = window.innerWidth - panelWidth - viewportPadding;
      const maxTop = window.innerHeight - panelHeight - viewportPadding;
      const nextLeft = Math.max(
        viewportPadding,
        Math.min(maxLeft, previewDragStart.current.left + dx),
      );
      const nextTop = Math.max(
        viewportPadding,
        Math.min(maxTop, previewDragStart.current.top + dy),
      );
      setPreviewPosition({ left: nextLeft, top: nextTop });
    };

    const handleUp = () => {
      setIsDraggingPreview(false);
      previewDragStart.current = null;
    };

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [isDraggingPreview]);

  // PDFs are rendered page-by-page as plain images (see pdfPageImageUrl) so the
  // existing zoom/pan image viewer just works for them too — find out how many
  // pages this PDF has whenever a new one is opened, and reset back to page 1.
  useEffect(() => {
    let cancelled = false;
    if (!previewUrl || GetPreviewKind(previewUrl) !== "pdf") {
      setPreviewPdfPage(1);
      setPreviewPdfPageCount(1);
      return;
    }
    setPreviewPdfPage(1);
    void fetchPdfPageCount(PathBasename(previewUrl))
      .then((count) => {
        if (!cancelled) setPreviewPdfPageCount(count);
      })
      .catch(() => {
        if (!cancelled) setPreviewPdfPageCount(1);
      });
    return () => {
      cancelled = true;
    };
  }, [previewUrl]);

  useEffect(() => {
    let cancelled = false;
    if (!jsonEditorPreviewUrl || GetPreviewKind(jsonEditorPreviewUrl) !== "pdf") {
      setJsonEditorPdfPage(1);
      setJsonEditorPdfPageCount(1);
      return;
    }
    setJsonEditorPdfPage(1);
    void fetchPdfPageCount(PathBasename(jsonEditorPreviewUrl))
      .then((count) => {
        if (!cancelled) setJsonEditorPdfPageCount(count);
      })
      .catch(() => {
        if (!cancelled) setJsonEditorPdfPageCount(1);
      });
    return () => {
      cancelled = true;
    };
  }, [jsonEditorPreviewUrl]);

  const loadInvoices = useCallback(async (options?: { silent?: boolean }) => {
    const silent = options?.silent ?? false;
    try {
      if (!silent) {
        setLoading(true);
      }
      setError(null);
      const data: InvoiceListResponse = await fetchInvoices({
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        file_status: fileStatusFilter || undefined,
      });
      setAllInvoices(data.items);
      const items = ApplyLocalFilters(data.items, {
        file_status: fileStatusFilter,
        search_field: searchField,
        search_value: searchValue,
        hitl_only: showHitlOnly,
      });
      setInvoices(items);
      setLastRefreshedAt(new Date());
    } catch (e) {
      if (!silent) {
        setError(e instanceof Error ? e.message : "Failed to load invoices");
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, [startDate, endDate, fileStatusFilter, searchField, searchValue, showHitlOnly]);

  const loadLicenseProfile = async () => {
    try {
      const data = await fetchLicenseProfile();
      setLicenseProfile(data);
    } catch {
      // Non-blocking: dashboard still works if license endpoint fails.
    }
  };

  const loadConfigStatus = async () => {
    try {
      const data = await fetchConfigStatus();
      setConfigStatus(data);
    } catch {
      // Non-blocking: if the check itself fails, don't block the home screen.
      setConfigStatus(null);
    }
  };

  const handleDeleteSelected = async () => {
    if (selectedInvoiceIds.length === 0) return;

    const ok = window.confirm(
      `Delete ${selectedInvoiceIds.length} invoice(s) from MongoDB?`,
    );
    if (!ok) return;

    try {
      setDeleting(true);
      setError(null);

      await deleteInvoices(selectedInvoiceIds);
      setSelectForDelete(false);
      setSelectedInvoiceIds([]);
      await loadInvoices();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete invoices");
    } finally {
      setDeleting(false);
    }
  };

  const selectedInvoiceIdSet = new Set(selectedInvoiceIds);

  const toggleInvoiceSelected = (invoiceId: string) => {
    setSelectedInvoiceIds((prev) => {
      if (prev.includes(invoiceId)) {
        return prev.filter((id) => id !== invoiceId);
      }
      return [...prev, invoiceId];
    });
  };

  const toggleInvoiceExpanded = (invoiceId: string) => {
    setExpandedInvoiceIds((prev) =>
      prev.includes(invoiceId)
        ? prev.filter((id) => id !== invoiceId)
        : [...prev, invoiceId],
    );
  };

  const clearFilters = async () => {
    setStartDate("");
    setEndDate("");
    setFileStatusFilter("");
    setSelectForDelete(false);
    setSelectedInvoiceIds([]);
    setSearchField("");
    setSearchValue("");
    setSearchOptions([]);
    try {
      setLoading(true);
      setError(null);
      const data: InvoiceListResponse = await fetchInvoices({});
      setAllInvoices(data.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load invoices");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setInvoices(
      ApplyLocalFilters(allInvoices, {
        file_status: fileStatusFilter,
        search_field: searchField,
        search_value: searchValue,
        hitl_only: showHitlOnly,
      }),
    );
  }, [allInvoices, fileStatusFilter, searchField, searchValue, showHitlOnly]);

  useEffect(() => {
    if (!selectForDelete) {
      setSelectedInvoiceIds([]);
    }
  }, [selectForDelete]);

  useEffect(() => {
    const idSet = new Set(invoices.map((inv) => inv.id));
    setExpandedInvoiceIds((prev) => prev.filter((id) => idSet.has(id)));
  }, [invoices]);

  useEffect(() => {
    let cancelled = false;
    if (!searchField) {
      setSearchOptions([]);
      return;
    }
    setLoadingSearchOptions(true);
    void fetchSearchValues(searchField)
      .then((values) => {
        if (cancelled) return;
        setSearchOptions(values);
      })
      .catch(() => {
        if (cancelled) return;
        setSearchOptions([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingSearchOptions(false);
      });
    return () => {
      cancelled = true;
    };
  }, [searchField]);

  useEffect(() => {
    let cancelled = false;
    if (!searchField || !searchValue.trim()) return;
    const timer = window.setTimeout(() => {
      void fetchSearchSuggestions(searchField, searchValue)
        .then((values) => {
          if (cancelled) return;
          setSearchOptions(values);
        })
        .catch(() => {
          if (cancelled) return;
          setSearchOptions([]);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [searchField, searchValue]);

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    pipelineSnapshotRef.current = null;
    void loadInvoices();
    void loadLicenseProfile();
    void loadConfigStatus();
  }, []);

  useEffect(() => {
    let cancelled = false;

    const pollForUpdates = async () => {
      if (editing || jsonEditorOpen || uploading) {
        return;
      }

      try {
        const pipeline = await fetchPipelineStatus();
        if (cancelled) {
          return;
        }

        const snapshot = JSON.stringify({
          stored_total: pipeline.stored_total,
          stored_healthy: pipeline.stored_healthy,
          stored_errors: pipeline.stored_errors,
          hitl_flagged_total: pipeline.hitl_flagged_total,
          hitl_review_pending: pipeline.hitl_review_pending,
          in_process: pipeline.in_process,
          watcher_active: pipeline.watcher_active,
          queue_total: pipeline.queue_total,
          processed: pipeline.processed,
          error: pipeline.error,
          gemini_api_error: pipeline.gemini_api_error ?? 0,
        });

        if (pipelineSnapshotRef.current === null) {
          pipelineSnapshotRef.current = snapshot;
          return;
        }

        if (snapshot === pipelineSnapshotRef.current) {
          return;
        }

        pipelineSnapshotRef.current = snapshot;
        await loadInvoices({ silent: true });
        if (!cancelled) {
          void loadLicenseProfile();
        }
      } catch {
        // Background poll — keep the current table if a refresh fails.
      }
    };

    const intervalId = window.setInterval(() => {
      void pollForUpdates();
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [loadInvoices, editing, jsonEditorOpen, uploading]);

  const handleFileChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setUploading(true);
      setError(null);
      setStatus("1) Uploading file and running OCR / Gemini extraction…");
      const created = await uploadInvoice(file);
      setAllInvoices((prev) => [created, ...prev]);
      setStatus(
        "2) Invoice processed and saved to MongoDB.\n3) Table below is refreshed with the new record.",
      );
      void loadLicenseProfile();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
      setStatus(null);
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  };

  const handleCellChange = (
    id: string,
    field: keyof Pick<InvoiceSummary, EditableInvoiceField>,
    value: string,
    lineItemIndex?: number | null,
  ) => {
    const invoiceLevelFields = new Set<EditableInvoiceField>([
      "invoice_number",
      "total_amount",
      "invoice_date",
      "seller",
      "sub_total",
      "sgst_rate",
      "sgst_amount",
      "cgst_rate",
      "cgst_amount",
      "igst_rate",
      "igst_amount",
      "discount",
      "round_off",
    ]);

    setAllInvoices((prev) =>
      prev.map((inv) =>
        inv.id === id &&
        (invoiceLevelFields.has(field as never)
          ? true
          : (inv.line_item_index ?? null) === (lineItemIndex ?? null))
          ? { ...inv, [field]: value }
          : inv,
      ),
    );
  };

  const handleCellBlur = async (
    id: string,
    field: EditableInvoiceField,
    value: string,
    lineItemIndex: number | null | undefined,
  ) => {
    try {
      setError(null);
      await updateInvoice(id, {
        [field]: value,
        line_item_index: lineItemIndex ?? null,
      });
      await loadInvoices();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update invoice");
    } finally {
      setEditing(null);
    }
  };

  const invoiceGroups = useMemo(() => GroupInvoicesById(invoices), [invoices]);

  // Real-time JSON editor totals: recomputed on every keystroke so mismatches (and a
  // missing invoice date) are visible before the user saves, not only after.
  const jsonEditorExpectedTotal = useMemo(
    () => CalculateEditorExpectedTotal(jsonEditorLineItems, jsonEditorAdditionalFields),
    [jsonEditorLineItems, jsonEditorAdditionalFields],
  );
  const jsonEditorEnteredTotal = useMemo(
    () => ParseAmount(jsonEditorForm.total_amount),
    [jsonEditorForm.total_amount],
  );
  const jsonEditorTotalMismatch =
    jsonEditorExpectedTotal != null &&
    jsonEditorEnteredTotal != null &&
    Math.abs(jsonEditorExpectedTotal - jsonEditorEnteredTotal) > 0.02;
  const jsonEditorDateMissing = !jsonEditorForm.invoice_date.trim();

  const handleAddLineItem = async (invoiceId: string) => {
    try {
      setError(null);
      await addInvoiceLineItem(invoiceId, {});
      setExpandedInvoiceIds((prev) => (prev.includes(invoiceId) ? prev : [...prev, invoiceId]));
      await loadInvoices();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add line item");
    }
  };

  const handleDeleteLineItem = async (invoiceId: string, lineItemIndex: number | null | undefined) => {
    if (lineItemIndex == null) return;
    const ok = window.confirm("Delete this line item?");
    if (!ok) return;
    try {
      setError(null);
      await deleteInvoiceLineItem(invoiceId, lineItemIndex);
      setExpandedInvoiceIds((prev) => (prev.includes(invoiceId) ? prev : [...prev, invoiceId]));
      await loadInvoices();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete line item");
    }
  };

  const handleOpenJsonEditor = async (invoiceId: string) => {
    try {
      setError(null);
      setJsonEditorLoading(true);
      const data = await fetchInvoiceJsonEditor(invoiceId);
      const geminiJson = (data.gemini_json ?? {}) as Record<string, unknown>;
      const lineItemsRaw = Array.isArray(data.line_items) ? data.line_items : [];
      const additionalFieldsRaw =
        geminiJson.additional_fields && typeof geminiJson.additional_fields === "object"
          ? (geminiJson.additional_fields as Record<string, unknown>)
          : {};

      setJsonEditorBase(geminiJson);
      setJsonEditorForm({
        invoice_number: ToText(geminiJson.invoice_number),
        invoice_date: ToText(geminiJson.invoice_date),
        due_date: ToText(geminiJson.due_date),
        seller: ToText(geminiJson.seller),
        buyer: ToText(geminiJson.buyer),
        address: ToText(geminiJson.address),
        bank_name: ToText(geminiJson.bank_name),
        bank_address: ToText(geminiJson.bank_address),
        account_number: ToText(geminiJson.account_number),
        account_holder_name: ToText(geminiJson.account_holder_name),
        ifsc_code: ToText(geminiJson.ifsc_code),
        total_amount: ToText(geminiJson.total_amount),
        po_id: ToText(geminiJson.po_id),
        term_to_pay: ToText(geminiJson.term_to_pay),
      });
      setJsonEditorLineItems(
        lineItemsRaw.map((item) => {
          const li = item as Record<string, unknown>;
          return {
            hsn_number: ToText(li.hsn_number),
            service: ToText(li.service),
            quantity: ToText(li.quantity),
            unit: ToText(li.unit),
            price_per_unit: ToText(li.price_per_unit),
            amount: ToText(li.amount),
            tax_rate: ToText(li.tax_rate),
            tax_amount: ToText(li.tax_amount),
            amount_after_tax: ToText(li.amount_after_tax),
          };
        }),
      );
      setJsonEditorAdditionalFields(
        Object.entries(additionalFieldsRaw)
          .filter(([key]) => !["HITL", "status", "ever_hitl_true", "hitl_remark", "hitl_remarks"].includes(key))
          .map(([key, value]) => ({ key, value: ToText(value) })),
      );
      setJsonEditorPreviewUrl(
        data.uploaded_file_path ? `/api/raw/${PathBasename(data.uploaded_file_path)}` : null,
      );
      setJsonEditorPreviewZoom(1);
      setJsonEditorPreviewRotate(0);
      setJsonEditorInvoiceId(invoiceId);
      setJsonEditorOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load invoice editor");
    } finally {
      setJsonEditorLoading(false);
    }
  };

  // Deep-link support: other pages (e.g. the ERP table's Edit button) link here with
  // ?edit=<invoiceId> to jump straight into the JSON editor for that invoice. Clear the
  // param once handled so a refresh or back-navigation doesn't reopen it.
  useEffect(() => {
    const editId = searchParams.get("edit");
    if (!editId) return;
    void handleOpenJsonEditor(editId);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("edit");
        return next;
      },
      { replace: true },
    );
    // Intentionally only re-runs when the URL's search params change, not on every render
    // (handleOpenJsonEditor is redefined each render but isn't a meaningful dependency here).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const handleSaveJsonEditor = async () => {
    if (!jsonEditorInvoiceId) return;
    try {
      setError(null);
      setJsonEditorSaving(true);
      const nextAdditionalFields: Record<string, unknown> = {};
      for (const row of jsonEditorAdditionalFields) {
        const key = row.key.trim();
        if (!key) continue;
        nextAdditionalFields[key] = row.value;
      }
      const prevAdditional =
        jsonEditorBase.additional_fields && typeof jsonEditorBase.additional_fields === "object"
          ? (jsonEditorBase.additional_fields as Record<string, unknown>)
          : {};
      for (const lifecycleKey of ["HITL", "status", "ever_hitl_true", "deblurred_applied", "human_approved"]) {
        if (lifecycleKey in prevAdditional) {
          nextAdditionalFields[lifecycleKey] = prevAdditional[lifecycleKey];
        }
      }
      const summaryTotalAmount = CalculateEditorExpectedTotal(jsonEditorLineItems, jsonEditorAdditionalFields);
      if (summaryTotalAmount != null) {
        nextAdditionalFields.summary_total_amount = summaryTotalAmount.toFixed(2);
      }
      // Confirmed manual edit: store mandatory comment and clear HITL.
      nextAdditionalFields.human_approved = true;
      nextAdditionalFields.HITL = false;
      nextAdditionalFields.hitl_comment = saveComment.trim();
      const nextGeminiJson: Record<string, unknown> = {
        ...jsonEditorBase,
        invoice_number: jsonEditorForm.invoice_number,
        invoice_date: jsonEditorForm.invoice_date,
        due_date: jsonEditorForm.due_date || null,
        seller: jsonEditorForm.seller,
        buyer: jsonEditorForm.buyer,
        address: jsonEditorForm.address,
        bank_name: jsonEditorForm.bank_name,
        bank_address: jsonEditorForm.bank_address,
        account_number: jsonEditorForm.account_number,
        account_holder_name: jsonEditorForm.account_holder_name,
        ifsc_code: jsonEditorForm.ifsc_code,
        total_amount: jsonEditorForm.total_amount,
        po_id: jsonEditorForm.po_id,
        term_to_pay: jsonEditorForm.term_to_pay,
        line_items: jsonEditorLineItems,
        additional_fields: nextAdditionalFields,
      };
      await saveInvoiceJsonEditor(jsonEditorInvoiceId, {
        gemini_json: nextGeminiJson,
        line_items: jsonEditorLineItems as unknown as Record<string, unknown>[],
      });
      setJsonEditorOpen(false);
      setSaveConfirmOpen(false);
      setSaveComment("");
      setJsonEditorPreviewUrl(null);
      setJsonEditorPreviewZoom(1);
      setJsonEditorPreviewRotate(0);
      setJsonEditorInvoiceId(null);
      await loadInvoices();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save invoice editor");
    } finally {
      setJsonEditorSaving(false);
    }
  };

  const updateEditorField = (field: keyof InvoiceEditorFormState, value: string) => {
    setJsonEditorForm((prev) => ({ ...prev, [field]: value }));
  };

  const updateLineItemField = (index: number, field: keyof InvoiceEditorLineItem, value: string) => {
    setJsonEditorLineItems((prev) =>
      prev.map((item, idx) => {
        if (idx !== index) return item;
        const next: InvoiceEditorLineItem = { ...item, [field]: value };

        // Real-time calculation: keep amount and amount_after_tax in sync as the
        // underlying quantity/rate/tax values change, so mismatches are caught as
        // the user types rather than only after saving.
        if (field === "quantity" || field === "price_per_unit") {
          const qty = ParseAmount(next.quantity);
          const rate = ParseAmount(next.price_per_unit);
          if (qty != null && rate != null) {
            next.amount = (qty * rate).toFixed(2);
          }
        }

        if (
          field === "quantity" ||
          field === "price_per_unit" ||
          field === "amount" ||
          field === "tax_rate" ||
          field === "tax_amount"
        ) {
          const amount = ParseAmount(next.amount);
          const taxAmount = ParseAmount(next.tax_amount);
          const taxRate = ParseAmount(next.tax_rate);
          if (amount != null) {
            if (taxAmount != null) {
              next.amount_after_tax = (amount + taxAmount).toFixed(2);
            } else if (taxRate != null) {
              next.amount_after_tax = (amount * (1 + taxRate / 100)).toFixed(2);
            }
          }
        }

        return next;
      }),
    );
  };

  const addLineItemEditorRow = () => {
    setJsonEditorLineItems((prev) => [
      ...prev,
      {
        hsn_number: "",
        service: "",
        quantity: "",
        unit: "",
        price_per_unit: "",
        amount: "",
        tax_rate: "",
        tax_amount: "",
        amount_after_tax: "",
      },
    ]);
  };

  const removeLineItemEditorRow = (index: number) => {
    setJsonEditorLineItems((prev) => prev.filter((_, idx) => idx !== index));
  };

  const updateAdditionalField = (index: number, field: keyof InvoiceEditorAdditionalField, value: string) => {
    setJsonEditorAdditionalFields((prev) =>
      prev.map((row, idx) => (idx === index ? { ...row, [field]: value } : row)),
    );
  };

  const handleRequestSave = () => {
    setError(null);
    setSaveConfirmOpen(true);
  };

  return (
    <div className="panel">
      {licenseProfile && <PlanBanner profile={licenseProfile} />}

      {configStatus && !configStatus.configured && (
        <div className="alert alert-error">{configStatus.message}</div>
      )}

      <div className="panel-header">
        <div>
          <h2>Upload Document</h2>
          <p>Upload invoice files and view extracted data from MongoDB.</p>
        </div>
        <label className="upload-button">
          <input
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,.docx"
            onChange={handleFileChange}
            disabled={uploading}
          />
          {uploading ? "Uploading…" : "Upload Invoice"}
        </label>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {status && !error && (
        <div className="alert">
          {status.split("\n").map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      )}

      <section className="panel-section">
        <div className="panel-section-main">
          <div className="section-header filters-row">
            <div>
              <h3>Recent Invoices</h3>
              {lastRefreshedAt && (
                <p className="analytics-updated live-refresh-hint">
                  Live updates enabled
                  {" · "}
                  last refreshed {lastRefreshedAt.toLocaleTimeString()}
                </p>
              )}
              <div className="filters-inline">
                <label>
                  From
                  <input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </label>
                <label>
                  To
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                  />
                </label>
                  <label>
                    File Status
                    <select
                      value={fileStatusFilter}
                      onChange={(e) =>
                        setFileStatusFilter(
                          e.target.value as "" | "error" | "healthy file",
                        )
                      }
                    >
                      <option value="">All</option>
                      <option value="error">Error</option>
                      <option value="healthy file">Healthy file</option>
                    </select>
                  </label>
                <label>
                  Search by
                  <select
                    value={searchField}
                    onChange={(e) => {
                      setSearchField(e.target.value as SearchField | "");
                      setSearchValue("");
                    }}
                  >
                    <option value="">Select field</option>
                    <option value="invoice_number">Invoices</option>
                    <option value="hsn_value">HSN number</option>
                    <option value="seller">Seller name</option>
                    <option value="service_category">Service</option>
                  </select>
                </label>
                <div className="hit-segmented" role="group" aria-label="HITL filter">
                  <button
                    type="button"
                    className={`hit-seg-btn${!showHitlOnly ? " is-active" : ""}`}
                    onClick={() => setShowHitlOnly(false)}
                  >
                    All Table
                  </button>
                  <button
                    type="button"
                    className={`hit-seg-btn${showHitlOnly ? " is-active" : ""}`}
                    onClick={() => setShowHitlOnly(true)}
                  >
                    HITL
                  </button>
                </div>
                <label>
                  Search value
                  <input
                    type="text"
                    value={searchValue}
                    onChange={(e) => setSearchValue(e.target.value)}
                    disabled={!searchField}
                    placeholder={searchField ? "Type to search..." : "Select 'Search by' first"}
                    list="invoice-search-suggestions"
                  />
                  <datalist id="invoice-search-suggestions">
                    {searchOptions.map((opt) => (
                      <option key={opt} value={opt} />
                    ))}
                  </datalist>
                </label>
              </div>
            </div>
            <div className="filters-actions">
              <button type="button" onClick={() => void loadInvoices()} disabled={loading}>
                {loading ? "Refreshing…" : "Apply Filters"}
              </button>
              <button type="button" onClick={() => void clearFilters()} disabled={loading}>
                Clear Filters
              </button>
            </div>
            <div className="delete-actions">
              <label>
                <input
                  type="checkbox"
                  checked={selectForDelete}
                  disabled={loading || deleting || invoices.length === 0}
                  onChange={(e) => setSelectForDelete(e.target.checked)}
                />
                Select rows for delete
              </label>
              {selectedInvoiceIds.length > 0 && (
                <button type="button" onClick={() => void handleDeleteSelected()} disabled={loading || deleting}>
                  {deleting ? "Deleting…" : `Delete ${selectedInvoiceIds.length}`}
                </button>
              )}
            </div>
          </div>
          {searchField && (
            <div className="search-hint">
              {loadingSearchOptions
                ? "Loading search values from MongoDB..."
                : "Suggestions are fetched from MongoDB for selected search field."}
            </div>
          )}
          <div className={`table-wrapper${showHitlOnly ? " hit-mode" : ""}`}>
            <table>
              <thead>
                <tr>
                  <th>Invoice #</th>
                  <th>Total Amount</th>
                  <th>Date</th>
                  <th>Seller</th>
                  <th>Status</th>
                  {showHitlOnly && <th>Reason for Human approval</th>}
                  <th>Preview</th>
                </tr>
              </thead>
              <tbody>
                {invoiceGroups.length === 0 && !loading && (
                  <tr>
                    <td colSpan={showHitlOnly ? 7 : 6} className="empty-state">
                      No invoices yet. Upload one to get started.
                    </td>
                  </tr>
                )}
                {invoiceGroups.map((group) => {
                  const isExpanded = expandedInvoiceIds.includes(group.id);
                  const inv = group.rows[0];
                  const hasDetails = group.rows.length > 0;
                  const summaryTotalAmount = SumLineItemAmount(group.rows);
                  return (
                    <React.Fragment key={group.id}>
                      <tr className={selectedInvoiceIdSet.has(inv.id) ? "row-selected" : undefined}>
                        <td>
                          <div className="invoice-cell-main">
                            {selectForDelete && (
                              <input
                                type="checkbox"
                                checked={selectedInvoiceIdSet.has(inv.id)}
                                onChange={() => toggleInvoiceSelected(inv.id)}
                                disabled={deleting}
                                aria-label={`Select invoice ${inv.id}`}
                                style={{ marginRight: 10 }}
                              />
                            )}
                            {hasDetails ? (
                              <div className="invoice-actions-stack">
                                <button
                                  type="button"
                                  className="invoice-expand-toggle"
                                  onClick={() => toggleInvoiceExpanded(group.id)}
                                  aria-expanded={isExpanded}
                                  aria-label={isExpanded ? "Collapse invoice rows" : "Expand invoice rows"}
                                >
                                  {isExpanded ? "▾" : "▸"}
                                </button>
                                {showHitlOnly && (
                                  <button
                                    type="button"
                                    className="invoice-json-edit-btn"
                                    onClick={() => void handleOpenJsonEditor(group.id)}
                                    disabled={jsonEditorLoading}
                                    aria-label="Edit invoice JSON and line items"
                                    title="Edit JSON and line_items"
                                  >
                                    ✎
                                  </button>
                                )}
                              </div>
                            ) : (
                              <span className="invoice-expand-spacer" aria-hidden="true" />
                            )}
                            <EditableCell
                              id={inv.id}
                              field="invoice_number"
                              lineItemIndex={null}
                              value={inv.invoice_number != null ? String(inv.invoice_number) : ""}
                              editing={editing}
                              setEditing={setEditing}
                              onChange={handleCellChange}
                              onCommit={handleCellBlur}
                            />
                          </div>
                        </td>
                        <td>
                          <EditableCell
                            id={inv.id}
                            field="total_amount"
                            lineItemIndex={null}
                            value={inv.total_amount != null ? String(inv.total_amount) : ""}
                            editing={editing}
                            setEditing={setEditing}
                            onChange={handleCellChange}
                            onCommit={handleCellBlur}
                          />
                        </td>
                        <td>
                          <EditableCell
                            id={inv.id}
                            field="invoice_date"
                            lineItemIndex={null}
                            value={inv.invoice_date ?? ""}
                            editing={editing}
                            setEditing={setEditing}
                            onChange={handleCellChange}
                            onCommit={handleCellBlur}
                          />
                        </td>
                        <td>
                          <EditableCell
                            id={inv.id}
                            field="seller"
                            lineItemIndex={null}
                            value={inv.seller ?? ""}
                            editing={editing}
                            setEditing={setEditing}
                            onChange={handleCellChange}
                            onCommit={handleCellBlur}
                          />
                        </td>
                        <td>
                          {inv.status === 2
                            ? "HITL processed"
                            : inv.status === 1
                            ? "HITL process pending"
                            : "System processed"}
                        </td>
                          {showHitlOnly && (
                            <>
                              <td className="hitl-reason-cell" title={inv.hitl_remark ?? undefined}>
                                {inv.hitl ? (
                                  inv.hitl_remarks && inv.hitl_remarks.length > 0 ? (
                                    <ul className="hitl-reason-list">
                                      {inv.hitl_remarks.map((reason, idx) => (
                                        <li key={idx}>{reason}</li>
                                      ))}
                                    </ul>
                                  ) : inv.hitl_remark && inv.hitl_remark.trim() ? (
                                    <ul className="hitl-reason-list">
                                      {inv.hitl_remark.split(";").map((reason, idx) => (
                                        <li key={idx}>{reason.trim()}</li>
                                      ))}
                                    </ul>
                                  ) : inv.deblurred_applied === true ? (
                                    "Low-quality scan - verify values"
                                  ) : (
                                    "Total amount mismatch"
                                  )
                                ) : (
                                  "—"
                                )}
                              </td>
                            </>
                          )}
                        <td>
                          {inv.uploaded_file_path ? (
                            (() => {
                              const url = `/api/raw/${PathBasename(inv.uploaded_file_path)}`;
                              const kind = GetPreviewKind(inv.uploaded_file_path);
                              if (kind === "image") {
                                return (
                                  <img
                                    src={url}
                                    alt="Invoice preview"
                                    className="preview-thumb"
                                    onClick={(e) => handleSelectPreview(url, e.currentTarget)}
                                  />
                                );
                              }
                              return (
                                <button
                                  type="button"
                                  className="preview-thumb-file"
                                  onClick={(e) => handleSelectPreview(url, e.currentTarget)}
                                  title={PathBasename(inv.uploaded_file_path)}
                                >
                                  {kind === "pdf" ? "PDF" : "FILE"}
                                </button>
                              );
                            })()
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>

                      {isExpanded && (
                        <tr className="invoice-subtable-row">
                          <td colSpan={showHitlOnly ? 7 : 6}>
                            <div className="invoice-subtable-wrapper">
                              <div className="invoice-subtable-header">
                                <div className="invoice-subtable-title">Line items</div>
                                <button
                                  type="button"
                                  className="invoice-subtable-add"
                                  disabled
                                  title="Read-only mode"
                                >
                                  +
                                </button>
                              </div>
                              <table className="invoice-subtable">
                                <colgroup>
                                  <col className="col-service" />
                                  <col className="col-qty" />
                                  <col className="col-unit" />
                                  <col className="col-price" />
                                  <col className="col-amount" />
                                  <col className="col-taxrate" />
                                  <col className="col-taxamount" />
                                  <col className="col-aftertax" />
                                  <col className="col-actions" />
                                </colgroup>
                                <thead>
                                  <tr>
                                    <th>Service</th>
                                    <th>Quantity</th>
                                    <th>Unit</th>
                                    <th>Price/Unit</th>
                                    <th>Amount</th>
                                    <th>Tax rate</th>
                                    <th>Tax amount</th>
                                    <th>Amount after tax</th>
                                    <th className="invoice-subtable-actions" aria-label="Actions" />
                                  </tr>
                                </thead>
                                <tbody>
                                  {group.rows.map((li) => (
                                    <tr key={`${group.id}:${li.line_item_index ?? "root"}`}>
                                      <td>
                                        <EditableCell
                                          id={li.id}
                                          field="service_category"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.service_category ?? ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="subtable-num">
                                        <EditableCell
                                          id={li.id}
                                          field="quantity"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.quantity != null ? String(li.quantity) : ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="subtable-num">
                                        <EditableCell
                                          id={li.id}
                                          field="unit"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.unit != null ? String(li.unit) : ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="subtable-num">
                                        <EditableCell
                                          id={li.id}
                                          field="price_per_unit"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.price_per_unit != null ? String(li.price_per_unit) : ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="subtable-num">
                                        <EditableCell
                                          id={li.id}
                                          field="amount"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.amount != null ? String(li.amount) : ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="subtable-num">
                                        <EditableCell
                                          id={li.id}
                                          field="tax_rate"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.tax_rate != null ? String(li.tax_rate) : ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="subtable-num">
                                        <EditableCell
                                          id={li.id}
                                          field="tax_amount"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.tax_amount != null ? String(li.tax_amount) : ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="subtable-num">
                                        <EditableCell
                                          id={li.id}
                                          field="amount_after_tax"
                                          lineItemIndex={li.line_item_index ?? null}
                                          value={li.amount_after_tax != null ? String(li.amount_after_tax) : ""}
                                          editing={editing}
                                          setEditing={setEditing}
                                          onChange={handleCellChange}
                                          onCommit={handleCellBlur}
                                        />
                                      </td>
                                      <td className="invoice-subtable-actions">
                                        <button
                                          type="button"
                                          className="invoice-subtable-delete"
                                          disabled
                                          aria-label="Delete line item"
                                          title="Read-only mode"
                                        >
                                          🗑
                                        </button>
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <div className="billing-summary">
                                <h4>Billing Summary</h4>
                                <div className="billing-summary-grid">
                                  <div className="billing-summary-label">Sub Total</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="sub_total"
                                      lineItemIndex={null}
                                      value={inv.sub_total != null ? String(inv.sub_total) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">SGST Rate</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="sgst_rate"
                                      lineItemIndex={null}
                                      value={inv.sgst_rate != null ? String(inv.sgst_rate) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">SGST Amount</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="sgst_amount"
                                      lineItemIndex={null}
                                      value={inv.sgst_amount != null ? String(inv.sgst_amount) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">CGST Rate</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="cgst_rate"
                                      lineItemIndex={null}
                                      value={inv.cgst_rate != null ? String(inv.cgst_rate) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">CGST Amount</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="cgst_amount"
                                      lineItemIndex={null}
                                      value={inv.cgst_amount != null ? String(inv.cgst_amount) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">IGST Rate</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="igst_rate"
                                      lineItemIndex={null}
                                      value={inv.igst_rate != null ? String(inv.igst_rate) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">IGST Amount</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="igst_amount"
                                      lineItemIndex={null}
                                      value={inv.igst_amount != null ? String(inv.igst_amount) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">Discount</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="discount"
                                      lineItemIndex={null}
                                      value={inv.discount != null ? String(inv.discount) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">Round Off / Square Off</div>
                                  <div>
                                    <EditableCell
                                      id={group.id}
                                      field="round_off"
                                      lineItemIndex={null}
                                      value={inv.round_off != null ? String(inv.round_off) : ""}
                                      editing={editing}
                                      setEditing={setEditing}
                                      onChange={handleCellChange}
                                      onCommit={handleCellBlur}
                                    />
                                  </div>
                                  <div className="billing-summary-label">Total Amount</div>
                                  <div className="billing-summary-total">
                                    {inv.summary_total_amount != null
                                      ? String(inv.summary_total_amount)
                                      : summaryTotalAmount != null
                                      ? summaryTotalAmount.toFixed(2)
                                      : inv.total_amount != null
                                        ? String(inv.total_amount)
                                        : "—"}
                                  </div>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
                {invoiceGroups.length > 0 && (
                  <tr className="table-footer">
                    <td>Total</td>
                    <td>{SumInvoicesTotalAmount(invoiceGroups).toFixed(2)}</td>
                    <td colSpan={showHitlOnly ? 5 : 4}></td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {isPreviewOpen && (
          <aside
            className="panel-section-preview preview-popover"
            style={{ top: `${previewPosition.top}px`, left: `${previewPosition.left}px` }}
          >
            <div
              className={`preview-header${isDraggingPreview ? " is-dragging" : ""}`}
              onMouseDown={handlePreviewHeaderMouseDown}
            >
              <h3>Invoice Preview</h3>
              <button
                type="button"
                className="preview-close"
                onClick={() => {
                  setIsPreviewOpen(false);
                  setPreviewUrl(null);
                  setPreviewZoom(1);
                }}
              >
                ✕
              </button>
            </div>
            {!previewUrl && (
              <p className="preview-placeholder">
                Click a preview thumbnail in the table to view the full invoice here.
              </p>
            )}
            {previewUrl && (GetPreviewKind(previewUrl) === "image" || GetPreviewKind(previewUrl) === "pdf") && (
              <>
                {GetPreviewKind(previewUrl) === "pdf" && previewPdfPageCount > 1 && (
                  <div className="preview-pdf-pager">
                    <button
                      type="button"
                      onClick={() => setPreviewPdfPage((p) => Math.max(1, p - 1))}
                      disabled={previewPdfPage <= 1}
                      aria-label="Previous page"
                    >
                      ‹
                    </button>
                    <span>
                      Page {previewPdfPage} of {previewPdfPageCount}
                    </span>
                    <button
                      type="button"
                      onClick={() => setPreviewPdfPage((p) => Math.min(previewPdfPageCount, p + 1))}
                      disabled={previewPdfPage >= previewPdfPageCount}
                      aria-label="Next page"
                    >
                      ›
                    </button>
                  </div>
                )}
                <div className="preview-controls">
                  <button
                    type="button"
                    onClick={() =>
                      setPreviewZoom((z) => Math.min(3, +(z + 0.25).toFixed(2)))
                    }
                  >
                    +
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      setPreviewZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2)))
                    }
                  >
                    −
                  </button>
                  <button type="button" onClick={() => setPreviewZoom(1)}>
                    Reset
                  </button>
                </div>
                <div
                  className={`preview-image-wrapper${isPanning ? " is-panning" : ""}`}
                  ref={previewWrapperRef}
                  onMouseDown={handlePreviewMouseDown}
                  onMouseMove={handlePreviewMouseMove}
                  onMouseUp={handlePreviewMouseUp}
                  onMouseLeave={handlePreviewMouseUp}
                >
                  <img
                    src={
                      GetPreviewKind(previewUrl) === "pdf"
                        ? pdfPageImageUrl(PathBasename(previewUrl), previewPdfPage)
                        : previewUrl
                    }
                    alt="Selected invoice"
                    style={{
                      width: `${Math.round(previewZoom * 100)}%`,
                      height: "auto",
                    }}
                  />
                </div>
              </>
            )}
            {previewUrl && GetPreviewKind(previewUrl) === "other" && (
              <div className="preview-unsupported">
                <p>Preview isn&apos;t available for this file type.</p>
                <a href={previewUrl} target="_blank" rel="noreferrer" className="preview-open-link">
                  Open {PathBasename(previewUrl)}
                </a>
              </div>
            )}
          </aside>
        )}

        {jsonEditorOpen && (
          <div className="json-editor-backdrop" role="dialog" aria-modal="true" aria-label="Invoice JSON editor">
            <div className="json-editor-modal">
              <div className="json-editor-header">
                <h3>Edit Invoice JSON</h3>
                <button
                  type="button"
                  className="preview-close"
                  onClick={() => {
                    if (jsonEditorSaving) return;
                    setJsonEditorOpen(false);
                    setJsonEditorPreviewUrl(null);
                    setJsonEditorPreviewZoom(1);
                    setJsonEditorPreviewRotate(0);
                    setJsonEditorInvoiceId(null);
                  }}
                >
                  ✕
                </button>
              </div>
              <div className="json-editor-split">
                <div className="json-editor-main">
              <p className="json-editor-note">Edit invoice details, line items, and additional fields.</p>
              <div className="json-editor-form-grid">
                <label className="json-editor-label">
                  Invoice Number
                  <input value={jsonEditorForm.invoice_number} onChange={(e) => updateEditorField("invoice_number", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  PO ID
                  <span className="json-editor-field-hint">
                    If PO ID is not available, mark PO ID as "Not Applicable".
                  </span>
                  <input value={jsonEditorForm.po_id} onChange={(e) => updateEditorField("po_id", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Invoice Date
                  <input value={jsonEditorForm.invoice_date} onChange={(e) => updateEditorField("invoice_date", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Due Date
                  <span className="json-editor-field-hint">
                    Provide either Due Date or Term to Pay to clear this flag.
                  </span>
                  <input value={jsonEditorForm.due_date} onChange={(e) => updateEditorField("due_date", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Term to Pay
                  <span className="json-editor-field-hint">
                    e.g. "Net 30", "30 days", "60 days" — or fill in Due Date instead.
                  </span>
                  <input value={jsonEditorForm.term_to_pay} onChange={(e) => updateEditorField("term_to_pay", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Seller
                  <input value={jsonEditorForm.seller} onChange={(e) => updateEditorField("seller", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Buyer
                  <input value={jsonEditorForm.buyer} onChange={(e) => updateEditorField("buyer", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Total Amount
                  <input value={jsonEditorForm.total_amount} onChange={(e) => updateEditorField("total_amount", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Bank Name
                  <input value={jsonEditorForm.bank_name} onChange={(e) => updateEditorField("bank_name", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  IFSC
                  <input value={jsonEditorForm.ifsc_code} onChange={(e) => updateEditorField("ifsc_code", e.target.value)} />
                </label>
                <label className="json-editor-label json-editor-span-2">
                  Address
                  <input value={jsonEditorForm.address} onChange={(e) => updateEditorField("address", e.target.value)} />
                </label>
              </div>

              <div className="json-editor-section-header">
                <h4>Line Items</h4>
                <button type="button" onClick={addLineItemEditorRow}>+ Add line item</button>
              </div>
              <div className="json-editor-line-items-table-wrap">
                <table className="json-editor-line-items-table">
                  <thead>
                    <tr>
                      <th>HSN</th>
                      <th>Service</th>
                      <th>Qty</th>
                      <th>Unit</th>
                      <th>Price/Unit</th>
                      <th>Amount</th>
                      <th>Tax Rate</th>
                      <th>Tax Amount</th>
                      <th>Amount After Tax</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jsonEditorLineItems.length === 0 && (
                      <tr>
                        <td colSpan={10} className="json-editor-empty-cell">No line items. Add one.</td>
                      </tr>
                    )}
                    {jsonEditorLineItems.map((li, idx) => (
                      <tr key={`li-${idx}`}>
                        <td>
                          <input value={li.hsn_number} onChange={(e) => updateLineItemField(idx, "hsn_number", e.target.value)} />
                        </td>
                        <td>
                          <input value={li.service} onChange={(e) => updateLineItemField(idx, "service", e.target.value)} />
                        </td>
                        <td>
                          <input value={li.quantity} onChange={(e) => updateLineItemField(idx, "quantity", e.target.value)} />
                        </td>
                        <td>
                          <input
                            value={li.unit}
                            placeholder="nos/ltr/kg..."
                            onChange={(e) => updateLineItemField(idx, "unit", e.target.value)}
                          />
                        </td>
                        <td>
                          <input value={li.price_per_unit} onChange={(e) => updateLineItemField(idx, "price_per_unit", e.target.value)} />
                        </td>
                        <td>
                          <input value={li.amount} onChange={(e) => updateLineItemField(idx, "amount", e.target.value)} />
                        </td>
                        <td>
                          <input value={li.tax_rate} onChange={(e) => updateLineItemField(idx, "tax_rate", e.target.value)} />
                        </td>
                        <td>
                          <input value={li.tax_amount} onChange={(e) => updateLineItemField(idx, "tax_amount", e.target.value)} />
                        </td>
                        <td>
                          <input value={li.amount_after_tax} onChange={(e) => updateLineItemField(idx, "amount_after_tax", e.target.value)} />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="json-editor-delete-icon-btn"
                            onClick={() => removeLineItemEditorRow(idx)}
                            aria-label="Delete line item"
                            title="Delete line item"
                          >
                            🗑
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div
                className={`json-editor-live-total${
                  jsonEditorTotalMismatch ? " mismatch" : jsonEditorExpectedTotal != null ? " ok" : ""
                }`}
              >
                <div className="json-editor-live-total-row">
                  <span>Computed total (line items + tax/discount/round-off)</span>
                  <strong>{jsonEditorExpectedTotal != null ? jsonEditorExpectedTotal.toFixed(2) : "—"}</strong>
                </div>
                {jsonEditorTotalMismatch && jsonEditorExpectedTotal != null && jsonEditorEnteredTotal != null && (
                  <div className="json-editor-live-total-row">
                    <span>Total Amount field mismatch</span>
                    <strong>
                      {jsonEditorEnteredTotal.toFixed(2)} vs {jsonEditorExpectedTotal.toFixed(2)}
                    </strong>
                  </div>
                )}
              </div>
              {jsonEditorDateMissing && (
                <div className="json-editor-live-warning">Invoice date is missing - this will be flagged for review.</div>
              )}

              <div className="json-editor-section-header">
                <h4>Additional Fields</h4>
              </div>
              <div className="json-editor-additional-grid">
                {jsonEditorAdditionalFields.map((row, idx) => (
                  <div key={`af-${idx}`} className="json-editor-additional-card">
                    <label className="json-editor-label">
                      {FormatAdditionalFieldLabel(row.key)}
                    <input
                      placeholder="value"
                      value={row.value}
                      readOnly={row.key === "deblurred_applied"}
                      onChange={(e) => updateAdditionalField(idx, "value", e.target.value)}
                    />
                    </label>
                  </div>
                ))}
              </div>
              <div className="json-editor-actions">
                <button
                  type="button"
                  onClick={() => {
                    if (jsonEditorSaving) return;
                    setJsonEditorOpen(false);
                    setJsonEditorPreviewUrl(null);
                    setJsonEditorPreviewZoom(1);
                    setJsonEditorPreviewRotate(0);
                    setJsonEditorInvoiceId(null);
                  }}
                >
                  Cancel
                </button>
                <button type="button" onClick={handleRequestSave} disabled={jsonEditorSaving}>
                  {jsonEditorSaving ? "Saving..." : "Save"}
                </button>
              </div>
                </div>
                <aside className="json-editor-preview-side">
                  <h4>Reference Preview</h4>
                  {jsonEditorPreviewUrl &&
                    (GetPreviewKind(jsonEditorPreviewUrl) === "image" ||
                      GetPreviewKind(jsonEditorPreviewUrl) === "pdf") && (
                    <>
                      {GetPreviewKind(jsonEditorPreviewUrl) === "pdf" && jsonEditorPdfPageCount > 1 && (
                        <div className="preview-pdf-pager">
                          <button
                            type="button"
                            onClick={() => setJsonEditorPdfPage((p) => Math.max(1, p - 1))}
                            disabled={jsonEditorPdfPage <= 1}
                            aria-label="Previous page"
                          >
                            ‹
                          </button>
                          <span>
                            Page {jsonEditorPdfPage} of {jsonEditorPdfPageCount}
                          </span>
                          <button
                            type="button"
                            onClick={() => setJsonEditorPdfPage((p) => Math.min(jsonEditorPdfPageCount, p + 1))}
                            disabled={jsonEditorPdfPage >= jsonEditorPdfPageCount}
                            aria-label="Next page"
                          >
                            ›
                          </button>
                        </div>
                      )}
                      <div className="json-editor-preview-controls">
                        <button
                          type="button"
                          onClick={() => setJsonEditorPreviewZoom((z) => Math.min(3, +(z + 0.25).toFixed(2)))}
                        >
                          +
                        </button>
                        <button
                          type="button"
                          onClick={() => setJsonEditorPreviewZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2)))}
                        >
                          -
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setJsonEditorPreviewZoom(1);
                            setJsonEditorPreviewRotate(0);
                          }}
                        >
                          Reset
                        </button>
                        <button
                          type="button"
                          onClick={() => setJsonEditorPreviewRotate((r) => (r + 90) % 360)}
                        >
                          Rotate
                        </button>
                      </div>
                      {/* Zoom is driven by an explicit width % (not CSS transform: scale) so the
                          scrollable wrap below actually grows and can be panned/scrolled to see
                          the zoomed-in area — a bare transform: scale() on this flex-centered,
                          overflow:auto container just clipped the image with no way to reach the
                          rest of it. Rotation still uses transform since it doesn't need scroll.
                          PDFs are rendered server-side to a PNG per page (see pdfPageImageUrl)
                          instead of an <iframe> — the app's own X-Frame-Options: DENY security
                          header was blocking the iframe from displaying the PDF at all, and this
                          also lets Prev/Next reuse the same zoom/pan viewer as JPEG/PNG. */}
                      <div className="json-editor-preview-image-wrap">
                        <img
                          src={
                            GetPreviewKind(jsonEditorPreviewUrl) === "pdf"
                              ? pdfPageImageUrl(PathBasename(jsonEditorPreviewUrl), jsonEditorPdfPage)
                              : jsonEditorPreviewUrl
                          }
                          alt="Invoice reference"
                          className="json-editor-preview-image"
                          style={{
                            width: `${Math.round(jsonEditorPreviewZoom * 100)}%`,
                            maxHeight: "none",
                            transform: `rotate(${jsonEditorPreviewRotate}deg)`,
                          }}
                        />
                      </div>
                    </>
                  )}
                  {jsonEditorPreviewUrl && GetPreviewKind(jsonEditorPreviewUrl) === "other" && (
                    <div className="json-editor-preview-empty">
                      <p>Preview isn&apos;t available for this file type.</p>
                      <a href={jsonEditorPreviewUrl} target="_blank" rel="noreferrer" className="preview-open-link">
                        Open {PathBasename(jsonEditorPreviewUrl)}
                      </a>
                    </div>
                  )}
                  {!jsonEditorPreviewUrl && (
                    <div className="json-editor-preview-empty">No preview image available.</div>
                  )}
                </aside>
              </div>
            </div>
          </div>
        )}

        {saveConfirmOpen && (
          <div className="json-editor-backdrop" role="dialog" aria-modal="true" aria-label="Confirm save with comment">
            <div className="json-editor-modal json-editor-confirm-modal">
              <div className="json-editor-header">
                <h3>Comment Required</h3>
                <button
                  type="button"
                  className="preview-close"
                  onClick={() => {
                    if (jsonEditorSaving) return;
                    setSaveConfirmOpen(false);
                  }}
                >
                  ✕
                </button>
              </div>
              <p className="json-editor-note">
                Enter a mandatory comment before confirming. On confirm, HITL will be set to false.
              </p>
              <label className="json-editor-label">
                Comment
                <textarea
                  className="json-editor-textarea"
                  value={saveComment}
                  onChange={(e) => setSaveComment(e.target.value)}
                  spellCheck={false}
                />
              </label>
              <div className="json-editor-actions">
                <button
                  type="button"
                  onClick={() => {
                    if (jsonEditorSaving) return;
                    setSaveConfirmOpen(false);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={jsonEditorSaving || saveComment.trim().length === 0}
                  onClick={() => void handleSaveJsonEditor()}
                >
                  {jsonEditorSaving ? "Saving..." : "Confirm"}
                </button>
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
};

function EditableCell(props: {
  id: string;
  field: EditableInvoiceField;
  lineItemIndex: number | null | undefined;
  value: string;
  editing: {
    id: string;
    field: EditableInvoiceField;
    lineItemIndex: number | null | undefined;
  } | null;
  setEditing: React.Dispatch<
    React.SetStateAction<{
      id: string;
      field: EditableInvoiceField;
      lineItemIndex: number | null | undefined;
    } | null>
  >;
  onChange: (
    id: string,
    field: keyof Pick<InvoiceSummary, EditableInvoiceField>,
    value: string,
    lineItemIndex?: number | null,
  ) => void;
  onCommit: (
    id: string,
    field: EditableInvoiceField,
    value: string,
    lineItemIndex: number | null | undefined,
  ) => Promise<void>;
}) {
  return (
    <div className="readonly-cell" title={props.value}>
      {props.value || "—"}
    </div>
  );
}

type PreviewKind = "image" | "pdf" | "other";

const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png"]);

function GetPreviewKind(path: string | null | undefined): PreviewKind {
  const name = PathBasename(path).toLowerCase();
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot) : "";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (ext === ".pdf") return "pdf";
  return "other";
}

function PathBasename(path: string | null | undefined): string {
  if (!path) return "";
  const parts = path.split(/[/\\]+/);
  return parts[parts.length - 1] || path;
}

function SumDisplayedTotalAmount(items: InvoiceSummary[]): number {
  let sum = 0;
  for (const inv of items) {
    const v = inv.total_amount;
    if (v == null) continue;
    const n = ParseAmount(v);
    if (n != null) sum += n;
  }
  return sum;
}

function SumInvoicesTotalAmount(groups: { id: string; rows: InvoiceSummary[] }[]): number {
  let sum = 0;
  for (const g of groups) {
    const first = g.rows[0];
    if (!first) continue;
    const n = ParseAmount(first.total_amount);
    if (n != null) sum += n;
  }
  return sum;
}

function SumLineItemAmount(items: InvoiceSummary[]): number | null {
  let sum = 0;
  let hasAny = false;
  for (const li of items) {
    const n = ParseAmount(li.amount_after_tax);
    if (n == null) continue;
    hasAny = true;
    sum += n;
  }
  return hasAny ? sum : null;
}

function ParseAmount(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const cleaned = value.replace(/,/g, "").trim();
    if (!cleaned) return null;
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function NormalizeEnum(value: unknown): string {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
}

function ToText(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

function FindAdditionalFieldValue(
  fields: InvoiceEditorAdditionalField[],
  keys: string[],
): number | null {
  for (const key of keys) {
    const row = fields.find((f) => f.key === key);
    if (row) {
      const parsed = ParseAmount(row.value);
      if (parsed != null) return parsed;
    }
  }
  return null;
}

// Mirrors backend/hitl_status.py:compute_expected_total - discount, round-off/square-off,
// and CGST/SGST/IGST can each live either inside the line items (amount_after_tax) or as
// invoice-level billing-summary fields; this avoids double-counting tax already embedded
// per line while still applying invoice-wide discount/round-off on top.
function CalculateEditorExpectedTotal(
  items: InvoiceEditorLineItem[],
  additionalFields: InvoiceEditorAdditionalField[],
): number | null {
  let amountAfterTaxSum = 0;
  let hasAmountAfterTax = false;
  for (const item of items) {
    const parsed = ParseAmount(item.amount_after_tax);
    if (parsed != null) {
      amountAfterTaxSum += parsed;
      hasAmountAfterTax = true;
    }
  }

  let base: number | null;
  if (hasAmountAfterTax) {
    base = amountAfterTaxSum;
  } else {
    let amountSum = 0;
    let hasAmount = false;
    for (const item of items) {
      const parsed = ParseAmount(item.amount);
      if (parsed != null) {
        amountSum += parsed;
        hasAmount = true;
      }
    }
    base = hasAmount
      ? amountSum
      : FindAdditionalFieldValue(additionalFields, ["sub_total", "subtotal_after_discount"]);
    if (base != null) {
      const sgst = FindAdditionalFieldValue(additionalFields, ["sgst_amount"]) ?? 0;
      const cgst = FindAdditionalFieldValue(additionalFields, ["cgst_amount"]) ?? 0;
      const igst = FindAdditionalFieldValue(additionalFields, ["igst_amount", "total_igst_amount"]) ?? 0;
      base = base + sgst + cgst + igst;
    }
  }

  if (base == null) return null;

  const discount = FindAdditionalFieldValue(additionalFields, ["discount", "discount_amount", "total_discount"]) ?? 0;
  const roundOff =
    FindAdditionalFieldValue(additionalFields, [
      "round_off",
      "roundoff",
      "round_off_amount",
      "square_off",
      "square_off_amount",
      "rounding",
    ]) ?? 0;
  return base - discount + roundOff;
}

function ApplyLocalFilters(
  items: InvoiceSummary[],
  filters: {
    file_status: "" | "error" | "healthy file";
    search_field: SearchField | "";
    search_value: string;
    hitl_only?: boolean;
  },
): InvoiceSummary[] {
  const fileStatus = filters.file_status;
  const searchField = filters.search_field;
  const searchNeedle = filters.search_value.trim().toLowerCase();
  const hitlOnly = filters.hitl_only ?? false;
  if (
    !fileStatus &&
    (!searchField || !searchNeedle) &&
    !hitlOnly
  ) {
    return items;
  }

  const searchFieldGetter: Record<SearchField, (inv: InvoiceSummary) => unknown> = {
    invoice_number: (inv) => inv.invoice_number,
    hsn_value: (inv) => inv.hsn_value,
    seller: (inv) => inv.seller,
    service_category: (inv) => inv.service_category,
  };

  return items.filter((inv) => {
    if (hitlOnly) {
      if (!inv.hitl) return false;
    }
    if (fileStatus) {
      // Mirror backend behavior: if Mongo doesn't have file_status, it should not match.
      if (!inv.file_status) return false;
      const f = NormalizeEnum(inv.file_status);
      const expected = NormalizeEnum(fileStatus);
      if (f !== expected) return false;
    }
    if (searchField && searchNeedle) {
      const currentValue = searchFieldGetter[searchField](inv);
      if (currentValue == null) return false;
      const haystack = String(currentValue).toLowerCase();
      if (!haystack.includes(searchNeedle)) return false;
    }
    return true;
  });
}

function GroupInvoicesById(items: InvoiceSummary[]): { id: string; rows: InvoiceSummary[] }[] {
  const map = new Map<string, InvoiceSummary[]>();
  const order: string[] = [];
  for (const item of items) {
    if (!map.has(item.id)) {
      map.set(item.id, []);
      order.push(item.id);
    }
    map.get(item.id)!.push(item);
  }
  return order.map((id) => ({ id, rows: map.get(id)! }));
}

function FormatAdditionalFieldLabel(key: string): string {
  const normalized = String(key || "").trim();
  if (!normalized) return "Additional Field";

  const acronyms: Record<string, string> = {
    gstin: "GSTIN",
    uin: "UIN",
    ifsc: "IFSC",
    hsn: "HSN",
    cin: "CIN",
    cgst: "CGST",
    sgst: "SGST",
    id: "ID",
  };

  return normalized
    .split("_")
    .filter(Boolean)
    .map((part) => {
      const lower = part.toLowerCase();
      if (acronyms[lower]) return acronyms[lower];
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}

export { Dashboard as Home, GroupInvoicesById, PathBasename, ParseAmount, SumInvoicesTotalAmount };
