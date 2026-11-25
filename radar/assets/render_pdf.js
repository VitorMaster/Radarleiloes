// Renderiza o HTML de impressão do relatório em PDF A4 (Chromium do Playwright).
// Uso: node render_pdf.js entrada.html saida.pdf
const { chromium } = require('playwright');
(async () => {
  const [input, output] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://' + input, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({ path: output, format: 'A4', printBackground: true, preferCSSPageSize: true,
                   margin: { top: 0, right: 0, bottom: 0, left: 0 } });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
