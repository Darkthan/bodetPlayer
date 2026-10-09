// Real browser -> local Windows agent -> real simulation server, using synthetic audio.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const page = await browser.newPage();
    await page.goto(process.env.BODET_TEST_ORIGIN);
    await page.locator('#password').fill(process.env.BODET_TEST_PASSWORD);
    await page.locator('#loginForm button').click();
    await page.locator('input[name=source][value=agent]').check();
    await page.locator('#agentPairCode').filter({hasText:'Agent de ce PC détecté'}).waitFor({timeout:15000});
    assert.equal(await page.locator('#windowsAgent option').count(), 2);
    await page.locator('#zones input').check();
    await page.locator('#start').click();
    await page.locator('#activity').filter({hasText:'Agent de test'}).waitFor();
    await page.locator('#stop').click();
    await page.locator('#activity').filter({hasText:'Aucune diffusion'}).waitFor();
    console.log('Real browser local-agent detection, start and stop passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
