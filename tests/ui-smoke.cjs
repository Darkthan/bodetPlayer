// Run with a local Playwright installation (or PLAYWRIGHT_MODULE pointing to it).
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1280, height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const state = {mode:'simulation', zones:[{id:1, name:'Salle'}], active:null,
      multicast_address:'239.1.2.3', player:null, audio:{block_ms:20, max_backlog_ms:200},
      agents:[{id:'pc1', name:'PC musique', online:true, status:'idle', error:''},
              {id:'pc2', name:'Autre PC', online:true, status:'idle', error:''}],
      playlists:[], library:[{id:'abcdefghijk', title:'Première chanson', size:1000},
               {id:'12345678901', title:'Deuxième chanson', size:2000}]};
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.hostname === '127.0.0.1' && url.pathname === '/identity') {
        await route.fulfill({json:{challenge:url.searchParams.get('challenge'), proof:'1'.repeat(64)},
          headers:{'Access-Control-Allow-Origin':'http://bodet-ui.test'}}); return;
      }
      if (url.pathname.startsWith('/api/')) {
        const body = url.pathname === '/api/library/upload' ? null : request.postDataJSON();
        if (url.pathname === '/api/library/upload') {
          const track = {id:`upload${state.library.length}`.padEnd(11, 'x'), title:url.searchParams.get('name'), size:1000};
          state.library.push(track);
          await route.fulfill({json:track}); return;
        }
        if (url.pathname === '/api/agents/local/challenge') {
          await route.fulfill({json:{challenge:'0'.repeat(64)}}); return;
        } else if (url.pathname === '/api/agents/local') {
          await route.fulfill({json:{id:'pc1'}}); return;
        } else if (url.pathname === '/api/agents/pc1/start') {
          state.active = {kind:'agent', agent_id:'pc1', source:'PC musique', zones:body.zones, bytes:96000};
          state.agents[0].status = 'capturing';
        } else if (url.pathname === '/api/agents/pc1' && request.method() === 'DELETE') {
          state.agents = state.agents.filter(agent => agent.id !== 'pc1');
        } else if (url.pathname === '/api/settings/audio') {
          state.audio = body;
          await route.fulfill({json:body}); return;
        } else if (url.pathname === '/api/playlists') {
          const playlist = {id:'saved1', ...body}; state.playlists.push(playlist);
          await route.fulfill({json:playlist}); return;
        } else if (url.pathname === '/api/playlists/saved1' && request.method() === 'PUT') {
          Object.assign(state.playlists[0], body);
          await route.fulfill({json:state.playlists[0]}); return;
        } else if (url.pathname === '/api/playlists/saved1' && request.method() === 'DELETE') {
          state.playlists = [];
        } else if (url.pathname === '/api/library/delete') {
          state.library = state.library.filter(t => !body.tracks.includes(t.id));
          state.playlists.forEach(p => p.tracks = p.tracks.filter(id => !body.tracks.includes(id)));
        } else if (url.pathname === '/api/library/start') {
          state.active = {kind:'playlist', source:'Bibliothèque', zones:body.zones, bytes:96000};
          state.player = {status:'playing', loop:body.loop, index:0, error:'', warning:'',
            queue:body.tracks.map((id, index) => ({...state.library.find(t => t.id === id), queue_id:'q' + index}))};
          state.player.title = state.player.queue[0].title;
        } else if (url.pathname === '/api/player/order') {
          const current = state.player.queue[state.player.index].queue_id;
          state.player.queue = body.order.map(id => state.player.queue.find(t => t.queue_id === id));
          state.player.index = body.order.indexOf(current);
        } else if (url.pathname === '/api/player/control') {
          if (body.action === 'loop') state.player.loop = body.loop;
          else state.player.index = (state.player.index + (body.action === 'next' ? 1 : -1) + state.player.queue.length) % state.player.queue.length;
          state.player.title = state.player.queue[state.player.index].title;
        } else if (url.pathname === '/api/stop') {
          state.active = null; state.player.status = 'stopped';
        } else if (request.method() === 'DELETE') {
          state.library = state.library.filter(t => t.id !== url.pathname.split('/').pop());
        }
        await route.fulfill({json:url.pathname === '/api/status' ? state : {ok:true}});
      } else {
        const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace('/static/', '');
        const contentType = relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html';
        await route.fulfill({body:fs.readFileSync(path.join(__dirname, '..', 'app', 'static', relative)), contentType});
      }
    });
    await page.goto('http://bodet-ui.test/');
    assert.equal(await page.locator('input[name=source]').count(), 4);
    assert.equal(await page.locator('#youtubeUrl').count(), 0);
    assert.equal(await page.locator('input[name=source][value=library]').isChecked(), true);
    assert.equal(await page.evaluate(() => window.isSecureContext), false);
    await page.locator('#settingsButton').click();
    await page.locator('#audioFast').click();
    await page.locator('#saveAudio').click();
    await page.locator('#audioStatus').filter({hasText:'Réglages enregistrés'}).waitFor();
    assert.deepEqual(state.audio, {block_ms:10, max_backlog_ms:100});
    await page.locator('#closeSettings').click();
    await page.locator('input[name=source][value=library]').check();
    await page.getByRole('button', {name:'Ajouter Première chanson à la file', exact:true}).click();
    await page.getByRole('button', {name:'Ajouter Deuxième chanson à la file', exact:true}).click();
    await page.getByRole('button', {name:'Monter Deuxième chanson', exact:true}).click();
    assert.deepEqual(await page.locator('.queue-title').allTextContents(), ['Deuxième chanson', 'Première chanson']);
    await page.locator('#zones input').check();
    await page.locator('#start').click();
    await page.locator('#playerTitle').filter({hasText:'Deuxième chanson'}).waitFor();
    assert.equal(state.player.queue[0].id, '12345678901');
    await page.locator('#settingsButton').click();
    assert.equal(await page.locator('#audioBlock').isDisabled(), true);
    await page.locator('#closeSettings').click();
    // Move the current track and verify playback remains on that track.
    await page.getByRole('button', {name:'Descendre Deuxième chanson', exact:true}).click();
    assert.equal(state.player.index, 1);
    assert.equal(await page.locator('#playerTitle').textContent(), 'Deuxième chanson');
    await page.locator('#playerQueue li').nth(1).dragTo(page.locator('#playerQueue li').nth(0));
    await page.waitForFunction(() => document.querySelector('.queue-title')?.textContent === 'Deuxième chanson');
    assert.equal(state.player.index, 0);
    const loopResponse = page.waitForResponse(response => response.url().endsWith('/api/player/control'));
    await page.locator('#playerLoop').selectOption('playlist');
    await loopResponse;
    assert.equal(state.player.loop, 'playlist');
    const nextResponse = page.waitForResponse(response => response.url().endsWith('/api/player/control'));
    await page.locator('#playerNext').click();
    await nextResponse;
    assert.equal(state.player.index, 1);
    const previousResponse = page.waitForResponse(response => response.url().endsWith('/api/player/control'));
    await page.locator('#playerPrevious').click();
    await previousResponse;
    assert.equal(state.player.index, 0);
    fs.mkdirSync(path.join(__dirname, '..', '.benchmarks'), {recursive:true});
    await page.screenshot({path:path.join(__dirname, '..', '.benchmarks', 'player-desktop.png'), fullPage:true});
    await page.setViewportSize({width:390, height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path:path.join(__dirname, '..', '.benchmarks', 'player-mobile.png'), fullPage:true});
    await page.locator('#playerStop').click();
    assert.equal(state.active, null);
    page.on('dialog', dialog => dialog.accept());
    await page.getByRole('button', {name:'Supprimer Première chanson de la bibliothèque', exact:true}).click();
    assert.equal(state.library.length, 1);
    await page.locator('#file').setInputFiles({name:'Mon son.wav', mimeType:'audio/wav', buffer:Buffer.from('test audio')});
    await page.locator('#uploadStatus').filter({hasText:'ajouté à la playlist'}).waitFor();
    assert.equal(state.library.length, 2);
    assert.ok((await page.locator('.queue-title').allTextContents()).includes('Mon son.wav'));
    const drop = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(new File(['audio'], 'Déposé.mp3', {type:'audio/mpeg'}));
      return transfer;
    });
    await page.locator('#fileDrop').dispatchEvent('drop', {dataTransfer:drop});
    await page.locator('#uploadStatus').filter({hasText:'Déposé.mp3 : ajouté'}).waitFor();
    assert.equal(new URL(page.url()).hostname, 'bodet-ui.test');
    assert.equal(state.library.length, 3);
    await page.locator('#playlistName').fill('Accueil');
    await page.locator('#savePlaylist').click();
    await page.waitForFunction(() => document.querySelector('#savedPlaylist').value === 'saved1');
    const savedOrder = [...state.playlists[0].tracks];
    await page.locator('#newPlaylist').click();
    assert.equal(await page.locator('.queue-title').count(), 0);
    await page.locator('#savedPlaylist').selectOption('saved1');
    assert.equal(await page.locator('.queue-title').count(), savedOrder.length);
    await page.getByRole('button', {name:'Monter Déposé.mp3', exact:true}).click();
    await page.locator('#playlistName').fill('Pause');
    await page.locator('#savePlaylist').click();
    await page.waitForFunction(() => document.querySelector('#message').textContent === 'Playlist enregistrée.');
    assert.equal(state.playlists[0].name, 'Pause');
    assert.notDeepEqual(state.playlists[0].tracks, savedOrder);
    await page.getByRole('checkbox', {name:'Sélectionner Mon son.wav', exact:true}).check();
    await page.getByRole('checkbox', {name:'Sélectionner Déposé.mp3', exact:true}).check();
    await page.locator('#deleteSelectedSounds').click();
    await page.waitForFunction(() => document.querySelector('#libraryCount').textContent === '1 piste(s)');
    assert.equal(state.library.length, 1);
    assert.equal(state.playlists[0].tracks.length, 1);
    await page.locator('#deletePlaylist').click();
    await page.waitForFunction(() => document.querySelector('#savedPlaylist').options.length === 1);
    assert.equal(state.library.length, 1);
    await page.locator('input[name=source][value=agent]').check();
    await page.locator('#agentPairCode').filter({hasText:'Agent de ce PC détecté'}).waitFor();
    assert.equal(await page.locator('#windowsAgent option').count(), 2);
    assert.equal(await page.locator('#windowsAgent').textContent().then(value => value.includes('Autre PC')), false);
    assert.equal(await page.locator('#pairWindowsAgent').count(), 0);
    assert.equal(await page.locator('.download-agent').getAttribute('href'), '/api/agents/download?server=http%3A%2F%2Fbodet-ui.test');
    await page.locator('#start').click();
    await page.locator('#activity').filter({hasText:'PC musique'}).waitFor();
    assert.equal(state.active.kind, 'agent');
    await page.locator('#stop').click();
    await page.waitForFunction(() => document.querySelector('#start').disabled === false);
    await page.getByRole('button', {name:'Retirer l’agent PC musique', exact:true}).click();
    await page.waitForFunction(() => document.querySelector('#windowsAgent').options.length === 1);
    assert.deepEqual(state.agents.map(agent => agent.id), ['pc2']);
    state.active = {kind:'agent', agent_id:'pc2', source:'Autre PC', zones:[1], bytes:96000};
    await page.evaluate(() => refresh());
    assert.equal(await page.locator('#stop').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('UI passed: HTTP playback, library, draft/live reorder, drag/drop, repeat, navigation, stop, delete, mobile layout.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
