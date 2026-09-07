import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  InvoiceSummary,
  InvoiceListResponse,
  ErpSyncSettings,
  TallyMasterSchedulerSettings,
  fetchInvoices,
  fetchErpSyncSettings,
  fetchTallyMasterSchedulerSettings,
  forceErpSync,
  saveErpSyncSettings,
  fetchInvoiceErpExport,
  pushInvoiceToTally,
} from "../api";
import { GroupInvoicesById, ParseAmount, SumInvoicesTotalAmount } from "./Dashboard";

// Round a rapidfuzz-style 0-100 score for display; null/undefined stay as "—".
function FormatScore(score: unknown): string {
  const n = ParseAmount(score);
  return n == null ? "—" : `${Math.round(n)}`;
}

const MatchTypeBadge: React.FC<{ matchType?: string | null }> = ({ matchType }) => {
  const type = matchType ?? "";
  if (type === "STOCK_ITEM") {
    return <span className="erp-match-type erp-match-type-stock">STOCK_ITEM</span>;
  }
  if (type === "LEDGER") {
    return <span className="erp-match-type erp-match-type-ledger">LEDGER</span>;
  }
  if (type === "UNMATCHED") {
    return <span className="erp-match-type erp-match-type-unmatched">UNMATCHED</span>;
  }
  return <span className="erp-match-type">—</span>;
};

const MatchScore: React.FC<{ score?: unknown; matched?: boolean }> = ({ score, matched }) => {
  const formatted = FormatScore(score);
  if (formatted === "—") {
    return <span className="erp-match-score-display">—</span>;
  }
  return (
    <span className={`erp-match-score-display${matched ? " erp-match-score-ok" : " erp-match-score-fail"}`}>
      {formatted}%
    </span>
  );
};

const MatchBadge: React.FC<{
  matched: boolean;
  label: string;
  score?: unknown;
  title?: string;
}> = ({ matched, label, score, title }) => (
  <span
    className={`erp-match-badge${matched ? " erp-match-badge-ok" : " erp-match-badge-fail"}`}
    title={title}
  >
    {label}
    {score !== undefined && <span className="erp-match-score">{FormatScore(score)}</span>}
  </span>
);

// Downloads the extracted invoice JSON (gemini_json + line_items) — not the raw uploaded
// file — since that's the data users actually want to pull out of the ERP match table.
const DownloadButton: React.FC<{
  invoiceId: string;
  invoiceNumber: unknown;
  downloadAllowed: boolean;
}> = ({ invoiceId, invoiceNumber, downloadAllowed }) => {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState(false);

  if (!downloadAllowed) {
    return (
      <span
        className="download-thumb download-thumb-disabled"
        title="Download is available once ERP matching completes with no unmatched vendor, PO, or line items."
        aria-disabled="true"
      >
        ⬇
      </span>
    );
  }

  const handleDownload = async () => {
    try {
      setDownloading(true);
      setDownloadError(false);
      const data = await fetchInvoiceErpExport(invoiceId);
      const payload = { ...data.gemini_json, line_items: data.line_items };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const safeName = (invoiceNumber != null ? String(invoiceNumber) : invoiceId).replace(
        /[^\w.-]+/g,
        "_",
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${safeName}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch {
      setDownloadError(true);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <button
      type="button"
      className="download-thumb"
      onClick={() => void handleDownload()}
      disabled={downloading}
      title={downloadError ? "Download failed — try again" : "Download invoice JSON"}
      aria-label="Download invoice JSON"
    >
      {downloading ? "…" : "⬇"}
    </button>
  );
};

function FormatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Never";
  return d.toLocaleString();
}

function FormatNextTallyRefresh(settings: TallyMasterSchedulerSettings | null): string {
  if (!settings?.tally_configured) return "—";
  if (settings.mode !== "scheduled") return "Manual only — configure on Tally Master Data";
  const next = settings.next_refresh_at ? new Date(settings.next_refresh_at) : null;
  if (!next || Number.isNaN(next.getTime())) return "Pending first refresh";
  return next.toLocaleString();
}

const ErpRemarkCell: React.FC<{
  inv: InvoiceSummary;
}> = ({ inv }) => {
  const [retrying, setRetrying] = useState(false);
  const remark = inv.erp_remark ?? "—";
  const isSuccess = remark === "Successful" || inv.tally_push_status === "success";
  const isFailed =
    remark.startsWith("Unsuccessful") ||
    inv.tally_push_status === "failed";
  const isPending = remark === "Pending Tally push";

  const handleRetry = async () => {
    try {
      setRetrying(true);
      await pushInvoiceToTally(inv.id, true);
    } catch {
      // Poll will refresh the row
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div className="erp-remark-cell">
      <span
        className={`erp-remark-badge${
          isSuccess ? " erp-remark-success" : isFailed ? " erp-remark-failed" : isPending ? " erp-remark-pending" : ""
        }`}
        title={inv.tally_error_reason ?? undefined}
      >
        {remark}
      </span>
      {isFailed && inv.erp_matching_complete && (
        <button
          type="button"
          className="erp-remark-retry"
          onClick={() => void handleRetry()}
          disabled={retrying}
          title="Retry Tally push"
          aria-label="Retry Tally push"
        >
          {retrying ? "…" : "↻"}
        </button>
      )}
    </div>
  );
};

export const Erp: React.FC = () => {
  const [allInvoices, setAllInvoices] = useState<InvoiceSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showNeedsReviewOnly, setShowNeedsReviewOnly] = useState(false);
  const [searchValue, setSearchValue] = useState("");
  const [expandedInvoiceIds, setExpandedInvoiceIds] = useState<string[]>([]);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null);

  const [erpSettings, setErpSettings] = useState<ErpSyncSettings | null>(null);
  const [tallyScheduler, setTallyScheduler] = useState<TallyMasterSchedulerSettings | null>(null);
  const [savingMergeSetting, setSavingMergeSetting] = useState(false);
  const [forcingSyncNow, setForcingSyncNow] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  const loadInvoices = useCallback(async (options?: { silent?: boolean }) => {
    const silent = options?.silent ?? false;
    try {
      if (!silent) setLoading(true);
      setError(null);
      const data: InvoiceListResponse = await fetchInvoices({});
      setAllInvoices(data.items);
      setLastRefreshedAt(new Date());
    } catch (e) {
      if (!silent) setError(e instanceof Error ? e.message : "Failed to load invoices");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadInvoices();
    const intervalId = window.setInterval(() => void loadInvoices({ silent: true }), 5000);
    return () => window.clearInterval(intervalId);
  }, [loadInvoices]);

  // Read-only poll of ERP sync status — gates downloads and status banners.
  const loadErpSettings = useCallback(async () => {
    try {
      const data = await fetchErpSyncSettings();
      setErpSettings(data);
    } catch {
      // Non-fatal for this page.
    }
  }, []);

  const loadTallyScheduler = useCallback(async () => {
    try {
      const data = await fetchTallyMasterSchedulerSettings();
      setTallyScheduler(data);
    } catch {
      // Non-fatal for this page.
    }
  }, []);

  useEffect(() => {
    void loadErpSettings();
    void loadTallyScheduler();
    const intervalId = window.setInterval(() => {
      void loadErpSettings();
      void loadTallyScheduler();
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [loadErpSettings, loadTallyScheduler]);

  const handleForceSync = async () => {
    try {
      setForcingSyncNow(true);
      setSyncMessage(null);
      setError(null);
      const data = await forceErpSync();
      setErpSettings(data);
      setSyncMessage("Re-match started — this page updates automatically.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start ERP re-match");
    } finally {
      setForcingSyncNow(false);
    }
  };

  const handleMergeSettingChange = async (enabled: boolean) => {
    if (!erpSettings) return;
    try {
      setSavingMergeSetting(true);
      setError(null);
      const data = await saveErpSyncSettings({
        mode: erpSettings.mode,
        frequency_minutes: erpSettings.frequency_minutes,
        merge_hitl_duplicates: enabled,
      });
      setErpSettings(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save merge setting");
    } finally {
      setSavingMergeSetting(false);
    }
  };

  // Downloads are only allowed once this invoice's ERP match is current and complete.
  // The API exposes erp_matching_complete per row; downloads use /erp-export (gated server-side).

  const toggleExpanded = (invoiceId: string) => {
    setExpandedInvoiceIds((prev) =>
      prev.includes(invoiceId) ? prev.filter((id) => id !== invoiceId) : [...prev, invoiceId],
    );
  };

  const filteredInvoices = useMemo(() => {
    const needle = searchValue.trim().toLowerCase();
    return allInvoices.filter((inv) => {
      if (showNeedsReviewOnly && !inv.hitl) return false;
      if (needle) {
        const haystack = `${inv.invoice_number ?? ""} ${inv.seller ?? ""} ${inv.po_id ?? ""}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [allInvoices, showNeedsReviewOnly, searchValue]);

  const invoiceGroups = useMemo(() => GroupInvoicesById(filteredInvoices), [filteredInvoices]);

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <h2>ERP — Master Data Matching</h2>
          <p>
            Each invoice&apos;s seller, line items, and PO number are fuzzy-matched against
            Tally master data stored in MongoDB (vendors, stock items, expense ledgers,
            purchase orders). Refresh master data from Settings → Tally Master Data.
            Anything that doesn&apos;t match sits in HITL with the reason shown below.
          </p>
        </div>
        <div className="panel-header-actions">
          <button
            type="button"
            onClick={() => void handleForceSync()}
            disabled={forcingSyncNow || !!erpSettings?.syncing || !erpSettings?.configured}
          >
            {erpSettings?.syncing ? "Re-matching…" : forcingSyncNow ? "Starting…" : "Force Re-match"}
          </button>
          <Link to="/settings/tally-masters" className="button-link">
            Tally Master Data
          </Link>
        </div>
      </div>

      {erpSettings?.configured && (
        <div className="erp-settings-sync-times">
          <label className="erp-merge-setting">
            <input
              type="checkbox"
              checked={erpSettings.merge_hitl_duplicates}
              disabled={savingMergeSetting || !!erpSettings.syncing}
              onChange={(e) => void handleMergeSettingChange(e.target.checked)}
            />
            <span>
              Merge duplicate invoice numbers into HITL pending before re-match (runs on Force
              Re-match and scheduled sync)
            </span>
          </label>
          <span className="erp-settings-last-synced">
            Last re-matched: {FormatTimestamp(erpSettings.last_synced_at)}
            {erpSettings.last_sync_result && (
              <>
                {" "}
                ({erpSettings.last_sync_result.scanned} scanned, {erpSettings.last_sync_result.updated}{" "}
                updated
                {(erpSettings.last_sync_result.merged_docs ?? 0) > 0
                  ? `, ${erpSettings.last_sync_result.merged_docs} merged`
                  : ""}
                {erpSettings.last_sync_result.errored > 0
                  ? `, ${erpSettings.last_sync_result.errored} errored`
                  : ""}
                )
              </>
            )}
          </span>
          <span className="erp-settings-next-synced">
            Next Tally refresh: {FormatNextTallyRefresh(tallyScheduler)}
          </span>
          {erpSettings.tally_configured && erpSettings.last_tally_synced_at && (
            <span className="erp-settings-last-synced">
              Last Tally push: {FormatTimestamp(erpSettings.last_tally_synced_at)}
              {erpSettings.last_tally_sync_result && (
                <>
                  {" "}
                  ({erpSettings.last_tally_sync_result.pushed ?? 0} pushed
                  {(erpSettings.last_tally_sync_result.errored ?? 0) > 0
                    ? `, ${erpSettings.last_tally_sync_result.errored} failed`
                    : ""}
                  )
                </>
              )}
            </span>
          )}
        </div>
      )}

      {error && <div className="alert alert-error">{error}</div>}

      {syncMessage && !error && <div className="alert">{syncMessage}</div>}

      {erpSettings && !erpSettings.configured && (
        <div className="alert">
          ERP master data is not loaded yet — open{" "}
          <Link to="/settings/tally-masters">Settings → Tally Master Data</Link> and click
          Refresh from Tally (or enable Scheduled refresh). Matching and invoice downloads stay
          disabled until master data is loaded.
        </div>
      )}
      {erpSettings?.configured && erpSettings.syncing && (
        <div className="alert">
          ERP sync in progress — invoice downloads unlock row-by-row once matching completes for
          each invoice.
        </div>
      )}

      <section className="panel-section">
        <div className="panel-section-main">
          <div className="section-header filters-row">
            <div>
              <h3>Invoices</h3>
              {lastRefreshedAt && (
                <p className="analytics-updated live-refresh-hint">
                  Live updates enabled · last refreshed {lastRefreshedAt.toLocaleTimeString()}
                </p>
              )}
              <div className="filters-inline">
                <label>
                  Search
                  <input
                    type="text"
                    value={searchValue}
                    onChange={(e) => setSearchValue(e.target.value)}
                    placeholder="Invoice #, seller, or PO ID..."
                  />
                </label>
                <div className="hit-segmented" role="group" aria-label="Match status filter">
                  <button
                    type="button"
                    className={`hit-seg-btn${!showNeedsReviewOnly ? " is-active" : ""}`}
                    onClick={() => setShowNeedsReviewOnly(false)}
                  >
                    All Table
                  </button>
                  <button
                    type="button"
                    className={`hit-seg-btn${showNeedsReviewOnly ? " is-active" : ""}`}
                    onClick={() => setShowNeedsReviewOnly(true)}
                  >
                    Needs Review
                  </button>
                </div>
              </div>
            </div>
            <div className="filters-actions">
              <button type="button" onClick={() => void loadInvoices()} disabled={loading}>
                {loading ? "Refreshing…" : "Refresh"}
              </button>
            </div>
          </div>

          <div className="table-wrapper erp-table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Invoice #</th>
                  <th>Total Amount</th>
                  <th>Seller / Vendor Match</th>
                  <th>PO / PO Match</th>
                  <th>Status</th>
                  <th>Match Reason</th>
                  <th>Download</th>
                  <th>ERP-Remark</th>
                </tr>
              </thead>
              <tbody>
                {invoiceGroups.length === 0 && !loading && (
                  <tr>
                    <td colSpan={8} className="empty-state">
                      No invoices match these filters.
                    </td>
                  </tr>
                )}
                {invoiceGroups.map((group) => {
                  const inv = group.rows[0];
                  const isExpanded = expandedInvoiceIds.includes(group.id);
                  const hasLineItems = group.rows.some((r) => r.line_item_index != null);

                  const vendorMatched = !!inv.vendor_id;
                  const poId = inv.po_id ?? null;
                  const poMatched = !!inv.po_business_unit;

                  return (
                    <React.Fragment key={group.id}>
                      <tr>
                        <td>
                          <div className="invoice-cell-main">
                            {hasLineItems ? (
                              <div className="invoice-actions-stack">
                                <button
                                  type="button"
                                  className="invoice-expand-toggle"
                                  onClick={() => toggleExpanded(group.id)}
                                  aria-expanded={isExpanded}
                                  aria-label={isExpanded ? "Collapse line items" : "Expand line items"}
                                >
                                  {isExpanded ? "▾" : "▸"}
                                </button>
                                {showNeedsReviewOnly && (
                                  <Link
                                    to={`/?edit=${encodeURIComponent(inv.id)}`}
                                    className="invoice-json-edit-btn"
                                    aria-label="Edit invoice data"
                                    title="Edit invoice data"
                                  >
                                    ✎
                                  </Link>
                                )}
                              </div>
                            ) : (
                              <span className="invoice-expand-spacer" aria-hidden="true" />
                            )}
                            {inv.invoice_number != null ? String(inv.invoice_number) : "—"}
                          </div>
                        </td>
                        <td>{inv.total_amount != null ? String(inv.total_amount) : "—"}</td>
                        <td>
                          <div className="erp-cell-stack">
                            <span>{inv.seller || "—"}</span>
                            <MatchBadge
                              matched={vendorMatched}
                              label={vendorMatched ? `${inv.vendor_id}` : "Unmatched"}
                              score={inv.vendor_match_score}
                              title={inv.vendor_match_name ?? undefined}
                            />
                          </div>
                        </td>
                        <td>
                          <div className="erp-cell-stack">
                            <span>{poId || "No PO ID"}</span>
                            {poId && (
                              <MatchBadge
                                matched={poMatched}
                                label={poMatched ? `${inv.po_business_unit}` : "Unmatched"}
                                score={inv.po_match_score}
                              />
                            )}
                          </div>
                        </td>
                        <td>
                          {inv.status === 2
                            ? "HITL processed"
                            : inv.status === 1
                            ? "HITL process pending"
                            : "System processed"}
                        </td>
                        <td className="hitl-reason-cell" title={inv.hitl_remark ?? undefined}>
                          {inv.hitl ? (
                            inv.hitl_remarks && inv.hitl_remarks.length > 0 ? (
                              <ul className="hitl-reason-list">
                                {inv.hitl_remarks.map((reason, idx) => (
                                  <li key={idx}>{reason}</li>
                                ))}
                              </ul>
                            ) : (
                              "—"
                            )
                          ) : (
                            "—"
                          )}
                        </td>
                        <td>
                          <DownloadButton
                            invoiceId={inv.id}
                            invoiceNumber={inv.invoice_number}
                            downloadAllowed={!!inv.erp_matching_complete}
                          />
                        </td>
                        <td>
                          <ErpRemarkCell inv={inv} />
                        </td>
                      </tr>

                      {isExpanded && (
                        <tr className="invoice-subtable-row">
                          <td colSpan={8}>
                            <div className="invoice-subtable-wrapper">
                              <div className="invoice-subtable-header">
                                <div className="invoice-subtable-title">Line items — original vs matched</div>
                              </div>
                              <table className="invoice-subtable invoice-subtable-match">
                                <thead>
                                  <tr>
                                    <th>Original item</th>
                                    <th>Matched item</th>
                                    <th>Type</th>
                                    <th>Score</th>
                                    <th>Qty</th>
                                    <th>Amount</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {group.rows.map((li) => {
                                    const matchType =
                                      li.match_type ?? li.line_match_type ?? null;
                                    const isMatched =
                                      matchType === "STOCK_ITEM" || matchType === "LEDGER";
                                    const originalName =
                                      li.original_name ?? li.service_category ?? "—";
                                    const matchedName = isMatched
                                      ? (li.matched_name ??
                                        li.item_id ??
                                        li.erp_ledger_name ??
                                        li.ledger_id ??
                                        "—")
                                      : "—";
                                    const score =
                                      li.match_score ??
                                      (matchType === "LEDGER"
                                        ? li.ledger_match_score
                                        : li.item_match_score);
                                    const qty =
                                      li.original_quantity ?? li.quantity ?? null;
                                    const amount =
                                      li.original_amount ?? li.amount ?? null;
                                    return (
                                      <tr key={`${group.id}:${li.line_item_index ?? "root"}`}>
                                        <td>{originalName}</td>
                                        <td>{matchedName}</td>
                                        <td>
                                          <MatchTypeBadge matchType={matchType} />
                                        </td>
                                        <td>
                                          <MatchScore score={score} matched={isMatched} />
                                        </td>
                                        <td className="subtable-num">
                                          {qty != null ? String(qty) : "—"}
                                        </td>
                                        <td className="subtable-num">
                                          {amount != null ? String(amount) : "—"}
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
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
                    <td colSpan={6}></td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
};
