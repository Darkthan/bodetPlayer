const playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const firefox = process.env.UI_TEST_BROWSER === 'firefox';
  const browser = await (firefox ? playwright.firefox : playwright.chromium).launch(firefox ? {headless:true} : {channel:'msedge', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1280, height:1000}});
    const errors = [], starts = [];
    page.on('pageerror', error => errors.push(error.message));
    let available = false;
    const state = {mode:'bodet', zones:[{id:1,name:'Salle'}], active:null, multicast_address:'239.1.2.3',
      audio:{block_ms:20,max_backlog_ms:200}, agents:[{id:'pc1',name:'PC musique',online:true,status:'idle',error:''},
      {id:'pc2',name:'Autre PC',online:true,status:'idle',error:''}]};
    await page.routeWebSocket('**/api/agents/browser', socket => {
      socket.onMessage(message => {
        const report = JSON.parse(message);
        if (report.agent_id) socket.send(JSON.stringify({browser_id:'a'.repeat(32)}));
      });
    });
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.hostname === '127.0.0.1') {
        await route.fulfill({status:available ? 200 : 503, json:{challenge:url.searchParams.get('challenge'),proof:'1'.repeat(64)},
          headers:{'Access-Control-Allow-Origin':'http://bodet-ui.test'}}); return;
      }
      if (url.pathname.startsWith('/api/')) {
        const body = request.postDataJSON();
        if (url.pathname === '/api/agents/local/challenge') {
          await route.fulfill({json:{challenge:'0'.repeat(64)}}); return;
        }
        if (url.pathname === '/api/agents/local') { await route.fulfill({json:{id:'pc1'}}); return; }
        if (url.pathname === '/api/agents/pc1/start') {
          starts.push(body); state.active = {kind:'agent',agent_id:'pc1',zones:body.zones,bytes:96000};
        } else if (url.pathname === '/api/stop') state.active = null;
        else if (url.pathname === '/api/settings/audio') state.audio = body;
        else if (url.pathname === '/api/settings/network') state.multicast_address = body.multicast_address;
        else if (url.pathname === '/api/settings/zones' && request.method() === 'POST') state.zones.push(body);
        else if (url.pathname.startsWith('/api/settings/zones/')) {
          const id = Number(url.pathname.split('/').pop());
          state.zones = state.zones.filter(zone => zone.id !== id);
          if (request.method() === 'PUT') state.zones.push(body);
        }
        await route.fulfill({json:url.pathname === '/api/status' ? state : {ok:true,multicast_address:state.multicast_address}}); return;
      }
      const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace('/static/', '');
      await route.fulfill({body:fs.readFileSync(path.join(__dirname,'..','app','static',relative)),
        contentType:relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html'});
    });
    await page.goto('http://bodet-ui.test/');
    await page.locator('#dashboard').waitFor();
    assert.equal(await page.locator('input[name=source]').count(), 0);
    assert.equal(await page.locator('#playerSettings').count(), 0);
    assert.equal(await page.getByText('Source audio',{exact:true}).count(), 0);
    assert.equal(await page.locator('#agentSettings').isVisible(), true);
    assert.equal(await page.locator('#manageDiffusion').isDisabled(), true);
    available = true;
    await page.locator('#checkLocalAgent').click();
    await page.locator('#agentName').filter({hasText:'PC musique'}).waitFor();
    assert.equal(await page.locator('#agentSettings').isHidden(), true);
    assert.equal(await page.getByText('Autre PC',{exact:true}).count(), 0);
    await page.locator('#manageDiffusion').click();
    await page.locator('#start').click();
    assert.equal(await page.locator('#zoneWarning').isVisible(), true);
    assert.match(await page.locator('#zoneWarning').textContent(), /Sélectionnez au moins une zone/);
    assert.equal(await page.locator('#zoneWarning').evaluate(node => node === document.activeElement), true);
    assert.equal(starts.length, 0);
    await page.locator('#zones input').check();
    assert.equal(await page.locator('#zoneWarning').isHidden(), true);
    await page.locator('#start').click();
    await page.locator('#agentState').filter({hasText:'Diffusion en cours'}).waitFor({state:'attached'});
    assert.deepEqual(starts,[{zones:[1],browser_id:'a'.repeat(32)}]);
    assert.equal(await page.locator('#start').isDisabled(), true);
    assert.equal(await page.locator('#stop').isEnabled(), true);
    await page.locator('#stop').click();
    await page.locator('#diffusionState').filter({hasText:'Aucune diffusion'}).waitFor();
    await page.locator('#closeDiffusion').click();
    await page.locator('#settingsButton').click();
    await page.locator('#audioFast').click(); await page.locator('#saveAudio').click();
    await page.locator('#audioStatus').filter({hasText:'Réglages enregistrés'}).waitFor();
    assert.deepEqual(state.audio,{block_ms:10,max_backlog_ms:100});
    await page.locator('#zoneNumber').fill('2'); await page.locator('#zoneName').fill('Cour');
    await page.locator('#saveZone').click();
    await page.getByRole('button',{name:'Modifier la zone Cour',exact:true}).click();
    await page.locator('#zoneName').fill('Préau'); await page.locator('#saveZone').click();
    await page.getByRole('button',{name:'Supprimer la zone Préau',exact:true}).click();
    await page.locator('#zoneStatus').filter({hasText:'Zone supprimée'}).waitFor();
    assert.deepEqual(state.zones,[{id:1,name:'Salle'}]);
    await page.locator('#closeSettings').click();
    fs.mkdirSync(path.join(__dirname,'..','.benchmarks'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'..','.benchmarks',`agent-ui-${firefox ? 'firefox' : 'edge'}.png`),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.locator('#manageDiffusion').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await page.screenshot({path:path.join(__dirname,'..','.benchmarks','agent-ui-mobile.png'),fullPage:true});
    state.active = {kind:'agent',agent_id:'pc2',zones:[1],bytes:96000};
    await page.evaluate(() => refresh());
    assert.equal(await page.locator('#stop').isDisabled(),true);
    assert.equal(await page.locator('#start').isDisabled(),true);
    assert.deepEqual(errors,[]);
    console.log('Agent UI passed: local detection, hidden setup, zone warning, start/stop, settings, mobile and other-PC protection.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
