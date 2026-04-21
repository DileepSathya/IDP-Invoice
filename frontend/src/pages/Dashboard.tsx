import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  InvoiceSummary,
  InvoiceListResponse,
  SearchField,
  fetchSearchSuggestions,
  fetchSearchValues,
  fetchInvoices,
  fetchInvoiceJsonEditor,
  uploadInvoice,
  updateInvoice,
  saveInvoiceJsonEditor,
  addInvoiceLineItem,
  deleteInvoiceLineItem,
  deleteInvoices,
} from "../api";

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
};

type InvoiceEditorLineItem = {
  hsn_number: string;
  service: string;
  quantity: string;
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

export const Dashboard: React.FC = () => {
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
  });
  const [jsonEditorLineItems, setJsonEditorLineItems] = useState<InvoiceEditorLineItem[]>([]);
  const [jsonEditorAdditionalFields, setJsonEditorAdditionalFields] = useState<InvoiceEditorAdditionalField[]>([]);
  const [jsonEditorLoading, setJsonEditorLoading] = useState(false);
  const [jsonEditorSaving, setJsonEditorSaving] = useState(false);
  const [saveConfirmOpen, setSaveConfirmOpen] = useState(false);
  const [saveComment, setSaveComment] = useState("");
  const [jsonEditorPreviewUrl, setJsonEditorPreviewUrl] = useState<string | null>(null);
  const [jsonEditorPreviewZoom, setJsonEditorPreviewZoom] = useState(1);
  const [jsonEditorPreviewRotate, setJsonEditorPreviewRotate] = useState(0);
  const [editing, setEditing] = useState<{
    id: string;
    field:
      | "invoice_number"
      | "total_amount"
      | "hsn_value"
      | "invoice_date"
      | "seller"
      | "service_category"
      | "quantity"
      | "price_per_unit"
      | "amount"
      | "tax_rate"
      | "tax_amount"
      | "amount_after_tax"
      | "sub_total"
      | "sgst_rate"
      | "sgst_amount"
      | "cgst_rate"
      | "cgst_amount";
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

  const loadInvoices = async () => {
    try {
      setLoading(true);
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
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load invoices");
    } finally {
      setLoading(false);
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
    void loadInvoices();
  }, []);

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
    field: keyof Pick<
      InvoiceSummary,
      | "invoice_number"
      | "total_amount"
      | "hsn_value"
      | "invoice_date"
      | "seller"
      | "service_category"
      | "quantity"
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
    >,
    value: string,
    lineItemIndex?: number | null,
  ) => {
    const invoiceLevelFields = new Set<
      | "invoice_number"
      | "total_amount"
      | "invoice_date"
      | "seller"
      | "sub_total"
      | "sgst_rate"
      | "sgst_amount"
      | "cgst_rate"
      | "cgst_amount"
    >([
      "invoice_number",
      "total_amount",
      "invoice_date",
      "seller",
      "sub_total",
      "sgst_rate",
      "sgst_amount",
      "cgst_rate",
      "cgst_amount",
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
    field:
      | "invoice_number"
      | "total_amount"
      | "hsn_value"
      | "invoice_date"
      | "seller"
      | "service_category"
      | "quantity"
      | "price_per_unit"
      | "amount"
      | "tax_rate"
      | "tax_amount"
      | "amount_after_tax"
      | "sub_total"
      | "sgst_rate"
      | "sgst_amount"
      | "cgst_rate"
      | "cgst_amount",
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
      });
      setJsonEditorLineItems(
        lineItemsRaw.map((item) => {
          const li = item as Record<string, unknown>;
          return {
            hsn_number: ToText(li.hsn_number),
            service: ToText(li.service),
            quantity: ToText(li.quantity),
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
          .filter(([key]) => !["HITL", "status", "ever_hitl_true"].includes(key))
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
      const summaryTotalAmount = CalculateEditorSummaryTotalAmount(jsonEditorLineItems);
      nextAdditionalFields.summary_total_amount = summaryTotalAmount.toFixed(2);
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
      prev.map((item, idx) => (idx === index ? { ...item, [field]: value } : item)),
    );
  };

  const addLineItemEditorRow = () => {
    setJsonEditorLineItems((prev) => [
      ...prev,
      {
        hsn_number: "",
        service: "",
        quantity: "",
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
                              <td>
                                {inv.hitl
                                  ? inv.deblurred_applied === true
                                    ? "file readability confidence is low"
                                    : "total amount mismatch"
                                  : "—"}
                              </td>
                            </>
                          )}
                        <td>
                          {inv.uploaded_file_path ? (
                            <img
                              src={`/api/raw/${PathBasename(inv.uploaded_file_path)}`}
                              alt="Invoice preview"
                              className="preview-thumb"
                              onClick={(e) =>
                                handleSelectPreview(
                                  `/api/raw/${PathBasename(inv.uploaded_file_path)}`,
                                  e.currentTarget,
                                )
                              }
                            />
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
            {previewUrl && (
              <>
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
                    src={previewUrl}
                    alt="Selected invoice"
                    style={{
                      width: `${Math.round(previewZoom * 100)}%`,
                      height: "auto",
                    }}
                  />
                </div>
              </>
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
                  Invoice Date
                  <input value={jsonEditorForm.invoice_date} onChange={(e) => updateEditorField("invoice_date", e.target.value)} />
                </label>
                <label className="json-editor-label">
                  Due Date
                  <input value={jsonEditorForm.due_date} onChange={(e) => updateEditorField("due_date", e.target.value)} />
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
                        <td colSpan={9} className="json-editor-empty-cell">No line items. Add one.</td>
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
              <div className="json-editor-summary-total">
                Summary Total Amount: {CalculateEditorSummaryTotalAmount(jsonEditorLineItems).toFixed(2)}
              </div>

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
                  {jsonEditorPreviewUrl ? (
                    <>
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
                      <div className="json-editor-preview-image-wrap">
                        <img
                          src={jsonEditorPreviewUrl}
                          alt="Invoice reference"
                          className="json-editor-preview-image"
                          style={{
                            transform: `scale(${jsonEditorPreviewZoom}) rotate(${jsonEditorPreviewRotate}deg)`,
                          }}
                        />
                      </div>
                    </>
                  ) : (
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
  field:
    | "invoice_number"
    | "total_amount"
    | "hsn_value"
    | "invoice_date"
    | "seller"
    | "service_category"
    | "quantity"
    | "price_per_unit"
    | "amount"
    | "tax_rate"
    | "tax_amount"
    | "amount_after_tax"
    | "sub_total"
    | "sgst_rate"
    | "sgst_amount"
    | "cgst_rate"
    | "cgst_amount";
  lineItemIndex: number | null | undefined;
  value: string;
  editing: {
    id: string;
    field:
      | "invoice_number"
      | "total_amount"
      | "hsn_value"
      | "invoice_date"
      | "seller"
      | "service_category"
      | "quantity"
      | "price_per_unit"
      | "amount"
      | "tax_rate"
      | "tax_amount"
      | "amount_after_tax"
      | "sub_total"
      | "sgst_rate"
      | "sgst_amount"
      | "cgst_rate"
      | "cgst_amount";
    lineItemIndex: number | null | undefined;
  } | null;
  setEditing: React.Dispatch<
    React.SetStateAction<{
      id: string;
      field:
        | "invoice_number"
        | "total_amount"
        | "hsn_value"
        | "invoice_date"
        | "seller"
        | "service_category"
        | "quantity"
        | "price_per_unit"
        | "amount"
        | "tax_rate"
        | "tax_amount"
        | "amount_after_tax"
        | "sub_total"
        | "sgst_rate"
        | "sgst_amount"
        | "cgst_rate"
        | "cgst_amount";
      lineItemIndex: number | null | undefined;
    } | null>
  >;
  onChange: (
    id: string,
    field: keyof Pick<
      InvoiceSummary,
      | "invoice_number"
      | "total_amount"
      | "hsn_value"
      | "invoice_date"
      | "seller"
      | "service_category"
      | "quantity"
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
    >,
    value: string,
    lineItemIndex?: number | null,
  ) => void;
  onCommit: (
    id: string,
    field:
      | "invoice_number"
      | "total_amount"
      | "hsn_value"
      | "invoice_date"
      | "seller"
      | "service_category"
      | "quantity"
      | "price_per_unit"
      | "amount"
      | "tax_rate"
      | "tax_amount"
      | "amount_after_tax"
      | "sub_total"
      | "sgst_rate"
      | "sgst_amount"
      | "cgst_rate"
      | "cgst_amount",
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

function CalculateEditorSummaryTotalAmount(items: InvoiceEditorLineItem[]): number {
  let total = 0;
  for (const item of items) {
    const parsed = ParseAmount(item.amount_after_tax);
    if (parsed != null) total += parsed;
  }
  return total;
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

