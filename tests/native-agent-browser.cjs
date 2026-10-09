// Real browser -> local Windows agent -> real simulation server, using synthetic audio.
const {chromium, firefox} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await (process.env.UI_TEST_BROWSER === 'firefox'
    ? firefox.launch({headless:true}) : chromium.launch({channel:'msedge', headless:true}));
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    // Point only the test browser at the isolated synthetic agent's listener.
    await page.context().addInitScript(port => {
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
    await page.locator('#start').click();
    await page.locator('#agentState').filter({hasText:'Diffusion en cours'}).waitFor({state:'attached'});
    const otherTab = await context.newPage();
    await otherTab.goto(process.env.BODET_TEST_ORIGIN);
    await otherTab.locator('#dashboard').waitFor();
    await otherTab.close();
    let status = await (await context.request.get(process.env.BODET_TEST_ORIGIN + '/api/status')).json();
    assert.ok(status.active, 'Closing a different tab must not stop the owner capture');
    const closedAt = Date.now();
    await page.close();
    do {
      status = await (await context.request.get(process.env.BODET_TEST_ORIGIN + '/api/status')).json();
      if (!status.active) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    } while (Date.now() - closedAt < 3000);
    assert.equal(status.active, null, 'Closing the owner tab must stop capture promptly');
    assert.equal(status.agents[0].online, true, 'The agent must stay available in the background');
    console.log('Real browser start/stop and owner-tab closure passed in ' + (Date.now() - closedAt) + ' ms.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
