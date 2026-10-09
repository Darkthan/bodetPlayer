// Real browser -> local Windows agent -> real simulation server, using synthetic audio.
const {chromium, firefox} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await (process.env.UI_TEST_BROWSER === 'firefox'
    ? firefox.launch({headless:true}) : chromium.launch({channel:'msedge', headless:true}));
  try {
    const page = await browser.newPage();
    // Point only the test browser at the isolated synthetic agent's listener.
    await page.addInitScript(port => {
      const originalFetch = window.fetch;
      window.fetch = (input, options) => originalFetch(typeof input === 'string'
        ? input.replace('http://127.0.0.1:17861/', 'http://127.0.0.1:' + port + '/') : input, options);
    }, process.env.BODET_TEST_IDENTITY_PORT || '17861');
    await page.goto(process.env.BODET_TEST_ORIGIN);
    await page.locator('#password').fill(process.env.BODET_TEST_PASSWORD);
    await page.locator('#loginForm button').click();
    await page.locator('#agentName').filter({hasText:'Agent de test'}).waitFor({timeout:15000});
    assert.equal(await page.locator('#agentSettings').isHidden(), true);
    await page.locator('#manageDiffusion').click();
    await page.locator('#start').click();
    assert.equal(await page.locator('#zoneWarning').isVisible(), true);
    await page.locator('#zones input').check();
    await page.locator('#start').click();
    await page.locator('#agentState').filter({hasText:'Diffusion en cours'}).waitFor({state:'attached'});
    await page.locator('#stop').click();
    await page.locator('#diffusionState').filter({hasText:'Aucune diffusion'}).waitFor();
    console.log('Real browser local-agent detection, start and stop passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
