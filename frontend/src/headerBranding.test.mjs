import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const mainSource = await readFile(new URL("./main.tsx", import.meta.url), "utf8");
const stylesSource = await readFile(new URL("./styles.css", import.meta.url), "utf8");

test("header renders the animated brand immediately before the product title", () => {
  assert.match(
    mainSource,
    /className="app-brand"[\s\S]*?<img[^>]+src=\{amogaBrand\}[^>]*>[\s\S]*?<h1>Intelligence Document Processing – Invoices<\/h1>/,
  );
});

test("animated header brand has responsive presentation styles", () => {
  assert.match(stylesSource, /\.app-brand-logo\s*\{[^}]*object-fit:\s*contain;/s);
  assert.match(stylesSource, /@media\s*\(max-width:\s*640px\)[\s\S]*?\.app-brand-logo/s);
});
