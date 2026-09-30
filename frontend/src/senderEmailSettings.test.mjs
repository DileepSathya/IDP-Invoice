import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const senderPage = await readFile(new URL("./pages/SenderEmailSettings.tsx", import.meta.url), "utf8");
const settingsLayout = await readFile(new URL("./components/SettingsLayout.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

test("sender email settings uses an accessible TLS switch", () => {
  assert.match(senderPage, /role="switch"/);
  assert.match(senderPage, /className="smtp-tls-toggle"/);
  assert.match(styles, /\.smtp-tls-toggle\s*\{/);
});

test("settings group buttons do not receive active styling", () => {
  assert.doesNotMatch(settingsLayout, /settings-nav-group-toggle\$\{active/);
});

test("settings child links use exact matching so only the current child is active", () => {
  assert.match(settingsLayout, /<NavLink key=\{child\.id\}[\s\S]*?end/);
});
