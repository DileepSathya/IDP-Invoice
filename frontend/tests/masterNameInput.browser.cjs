// Run with PLAYWRIGHT_MODULE pointing to an available Playwright installation.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { build } = require('esbuild');
const assert = require('node:assert/strict');

(async () => {
  const bundle = await build({
    stdin: {
      contents: `import React, {useState} from 'react';
        import {createRoot} from 'react-dom/client';
        import {MasterNameInput} from './src/components/MasterNameInput';
        function App() { const [seller, setSeller] = useState(''); const [item, setItem] = useState(''); const [ledger, setLedger] = useState('');
          return <><label>Seller<MasterNameInput kind="vendors" value={seller} onChange={setSeller}/></label>
            <label>Stock<MasterNameInput kind="items" value={item} onChange={setItem}/></label>
            <label>Ledger<MasterNameInput kind="ledgers" value={ledger} onChange={setLedger}/></label>
            <button onClick={() => setSeller('Automated seller')}>Fill automatically</button></>; }
        createRoot(document.getElementById('root')).render(<App/>);`,
      resolveDir: process.cwd(), loader: 'tsx',
    },
    bundle: true, write: false, jsx: 'automatic',
    loader: { '.css': 'empty' },
    plugins: [{ name: 'mock-search', setup(build) {
      build.onResolve({ filter: /^\.\.\/api$/ }, () => ({ path: 'api', namespace: 'mock' }));
      build.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ contents: `
        export async function fetchMasterSuggestions(kind, query) {
          await new Promise(resolve => setTimeout(resolve, query === 'slow' ? 600 : 10));
          if (query === 'error') throw new Error('offline');
          const names = kind === 'vendors' ? ['ACME & Co.', 'Beta Traders'] : kind === 'ledgers' ? ['Factory Repair Expenses'] : ['Bolt', 'Bolt Large'];
          return names.filter(name => name.toLowerCase().includes(query.toLowerCase()))
            .map(name => ({id:name, name, detail:'Master detail'}));
        }` }));
    }}],
  });
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage();
    await page.setContent('<div id="root"></div>');
    await page.addStyleTag({ path: 'src/components/MasterNameInput.css' });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    const seller = page.getByRole('combobox').nth(0);
    const stock = page.getByRole('combobox').nth(1);
    await seller.focus();
    await page.getByRole('option', { name: /ACME/ }).click();
    assert.equal(await seller.inputValue(), 'ACME & Co.');
    await page.mouse.move(1000, 700);
    await stock.fill('bolt');
    await page.getByRole('option').first().waitFor();
    await stock.press('ArrowDown');
    await stock.press('Enter');
    assert.equal(await stock.inputValue(), 'Bolt');
    assert.equal(await seller.inputValue(), 'ACME & Co.');
    await stock.fill('unlisted item');
    await page.getByRole('status').filter({ hasText: 'No matching' }).waitFor();
    assert.equal(await stock.inputValue(), 'unlisted item');
    await stock.press('Escape');
    assert.equal(await stock.getAttribute('aria-expanded'), 'false');
    await seller.fill('slow');
    await page.waitForTimeout(250);
    await seller.fill('Beta');
    await page.getByRole('option', { name: /Beta/ }).waitFor();
    await page.waitForTimeout(650);
    assert.equal(await page.getByRole('option').count(), 1);
    await seller.fill('error');
    await page.getByRole('status').filter({ hasText: 'unavailable' }).waitFor();
    assert.equal(await seller.inputValue(), 'error');
    await page.getByRole('button', { name: 'Fill automatically' }).click();
    await page.waitForFunction(() => document.querySelector('input').value === 'Automated seller');
    assert.equal(await seller.inputValue(), 'Automated seller');
    assert.equal(await seller.getAttribute('aria-expanded'), 'false');
    const ledger = page.getByRole('combobox').nth(2);
    await ledger.fill('Factory');
    await page.getByRole('listbox', { name: 'Tally expense ledgers' }).getByRole('option').click();
    assert.equal(await ledger.inputValue(), 'Factory Repair Expenses');
    assert.equal(await seller.inputValue(), 'Automated seller');
    console.log('PASS: mouse selection, keyboard selection, row isolation, free text, Escape, stale requests, lookup failure, automated filling');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
