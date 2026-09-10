import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function loadModule() {
  const source = await readFile(new URL("./invoiceTaxDetails.ts", import.meta.url), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
}

test("normalization always provides every tax and adjustment field", async () => {
  const { normalizeInvoiceTaxDetails } = await loadModule();
  const result = normalizeInvoiceTaxDetails({ purchase_order_type: "Domestic" });

  assert.deepEqual(result.taxDetails, {
    cgst_rate: "",
    cgst_amount: "",
    sgst_rate: "",
    sgst_amount: "",
    igst_rate: "",
    igst_amount: "",
    discount: "",
    round_off: "",
  });
  assert.deepEqual(result.remainingFields, { purchase_order_type: "Domestic" });
});

test("normalization moves legacy aliases into canonical fields without duplicates", async () => {
  const { normalizeInvoiceTaxDetails } = await loadModule();
  const result = normalizeInvoiceTaxDetails({
    total_igst_amount: "180.00",
    discount_amount: "25.00",
    square_off: "-0.50",
    vendor_id: "V-42",
  });

  assert.equal(result.taxDetails.igst_amount, "180.00");
  assert.equal(result.taxDetails.discount, "25.00");
  assert.equal(result.taxDetails.round_off, "-0.50");
  assert.deepEqual(result.remainingFields, { vendor_id: "V-42" });
});

test("normalization falls back to populated legacy aliases when canonical values are blank", async () => {
  const { normalizeInvoiceTaxDetails } = await loadModule();
  const result = normalizeInvoiceTaxDetails({
    igst_amount: null,
    total_igst_amount: "180.00",
    discount: "",
    discount_amount: "25.00",
    round_off: "   ",
    square_off: "-0.50",
  });

  assert.equal(result.taxDetails.igst_amount, "180.00");
  assert.equal(result.taxDetails.discount, "25.00");
  assert.equal(result.taxDetails.round_off, "-0.50");
});

test("serialization preserves unrelated fields and replaces aliases with canonical values", async () => {
  const { serializeInvoiceTaxDetails } = await loadModule();
  const result = serializeInvoiceTaxDetails(
    {
      total_igst_amount: "old",
      total_discount: "old",
      roundoff: "old",
      human_approved: true,
    },
    {
      cgst_rate: "9",
      cgst_amount: "90",
      sgst_rate: "9",
      sgst_amount: "90",
      igst_rate: "",
      igst_amount: "",
      discount: "10",
      round_off: "-0.25",
    },
  );

  assert.deepEqual(result, {
    human_approved: true,
    cgst_rate: "9",
    cgst_amount: "90",
    sgst_rate: "9",
    sgst_amount: "90",
    igst_rate: "",
    igst_amount: "",
    discount: "10",
    round_off: "-0.25",
  });
});

test("unchanged additional fields retain their original value types", async () => {
  const { serializeAdditionalFieldRows } = await loadModule();
  const metadata = { source: "ocr", confidence: 0.92 };
  const result = serializeAdditionalFieldRows([
    { key: "metadata", value: "[object Object]", originalValue: metadata, originalText: "[object Object]" },
  ]);
  assert.deepEqual(result, { metadata });
});

test("hidden review metadata survives editor saves", async () => {
  const { preserveInvoiceEditorMetadata } = await loadModule();
  const target = preserveInvoiceEditorMetadata(
    { discount: "10" },
    {
      HITL: true,
      status: "review",
      hitl_remark: "Check vendor",
      hitl_remarks: ["Check vendor", "Confirm tax"],
      unrelated: "not copied",
    },
  );

  assert.deepEqual(target, {
    discount: "10",
    HITL: true,
    status: "review",
    hitl_remark: "Check vendor",
    hitl_remarks: ["Check vendor", "Confirm tax"],
  });
});

test("HITL remark arrays render as a clean ordered list of reasons", async () => {
  const { normalizeHitlRemarks } = await loadModule();

  assert.deepEqual(
    normalizeHitlRemarks("Legacy reason", [" Check vendor ", "", "Confirm tax"]),
    ["Check vendor", "Confirm tax"],
  );
});

test("legacy semicolon-delimited HITL remarks become separate reasons", async () => {
  const { normalizeHitlRemarks } = await loadModule();

  assert.deepEqual(normalizeHitlRemarks("Check vendor; Confirm tax ; ", undefined), [
    "Check vendor",
    "Confirm tax",
  ]);
});

test("missing HITL remarks produce an empty reason list", async () => {
  const { normalizeHitlRemarks } = await loadModule();

  assert.deepEqual(normalizeHitlRemarks("   ", null), []);
});
