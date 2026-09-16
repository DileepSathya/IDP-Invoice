import assert from "node:assert/strict";
import test from "node:test";

import { settingsNavigation, settingsSectionForPath } from "./settingsNavigation.mjs";

test("settings navigation groups the three ERP child settings", () => {
  const erp = settingsNavigation.find((item) => item.label === "ERP Settings");

  assert.deepEqual(
    erp.children.map(({ label, to }) => ({ label, to })),
    [
      { label: "Company Details", to: "/settings/company" },
      { label: "Ledger Settings", to: "/settings/ledger" },
      { label: "Tally Master Data", to: "/settings/tally-masters" },
    ],
  );
});

test("settings paths resolve to the correct active section", () => {
  assert.equal(settingsSectionForPath("/settings"), "ai");
  assert.equal(settingsSectionForPath("/settings/notifications"), "notifications");
  assert.equal(settingsSectionForPath("/settings/company"), "company");
  assert.equal(settingsSectionForPath("/settings/ledger"), "ledger");
  assert.equal(settingsSectionForPath("/settings/tally-masters"), "tally-masters");
});
