const $ = id => document.getElementById(id);
let socket, context, stream, processor, objectURL, running = false, busy = false;
let networkDirty = false;
let youtubeSignature = '';
let librarySignature = '', draftQueue = [], musicStatus = null, draggedTrack = null, ordering = false;
let zonesSignature = '', currentZones = [], editingZone = null, settingsActive = false;
const message = text => { $('message').textContent = text; };
async function api(path, body, method = 'POST') {
  const response = await fetch('/api/' + path, body === undefined ? {} : {
    method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Erreur du serveur');
  return result;
}
async function refresh() {
  try {
    const status = await api('status');
    $('login').hidden = true; $('dashboard').hidden = false;
    $('settingsButton').hidden = false;
    settingsActive = !!status.active;
    $('zoneFields').disabled = settingsActive;
    $('settingsBusy').hidden = !settingsActive;
    if (!networkDirty) $('multicast').value = status.multicast_address;
    $('multicast').disabled = !!status.active;
    $('saveNetwork').disabled = !!status.active;
    $('mode').textContent = status.mode === 'simulation'
      ? 'Mode simulation : le son arrive au serveur, mais aucune enceinte ne reçoit de son. La passerelle Sigma / Harmonys reste à intégrer et valider.'
      : status.mode === 'bodet'
        ? 'Diffusion Bodet MEL/MP3 activée. Réception sur vos enceintes à valider.'
        : 'Passerelle audio configurée. Vérifiez la réception et les zones sur les enceintes avant utilisation.';
    const signature = JSON.stringify(status.zones);
    if (signature !== zonesSignature) {
      const selected = new Set([...$('zones').querySelectorAll('input:checked')].map(input => Number(input.value)));
      $('zones').replaceChildren();
      currentZones = status.zones; zonesSignature = signature;
      for (const zone of status.zones) {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox'; checkbox.value = zone.id;
      checkbox.checked = selected.has(zone.id);
      label.append(checkbox, document.createTextNode(' ' + zone.name + ' · ' + zone.id));
      $('zones').append(label);
      }
      if (!status.zones.length) $('zones').textContent = 'Aucune zone. Ajoutez-en dans les paramètres.';
      renderZoneList();
    }
    $('zoneList').querySelectorAll('button').forEach(button => button.disabled = settingsActive);
    $('activity').textContent = status.active
      ? `${status.active.source} → zones ${status.active.zones.join(', ')} · ${(status.active.bytes / 96000).toFixed(1)} s reçues${status.mode === 'simulation' ? ' (simulation)' : ''}`
      : 'Aucune diffusion en cours.';
    $('start').disabled = !!status.active || busy;
    renderYouTube(status);
  } catch (error) {
    $('login').hidden = false; $('dashboard').hidden = true;
    $('settingsButton').hidden = true;
    $('settingsDialog').close();
    if (running) await cleanup();
  }
}
async function cleanup() {
  running = false;
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  if (processor) { processor.disconnect(); processor = null; }
  if (stream) { stream.getTracks().forEach(track => track.stop()); stream = null; }
  if (context) { await context.close(); context = null; }
  $('audio').pause(); busy = false; $('start').disabled = false;
}
function renderYouTube(status) {
  musicStatus = status;
  const library = status.library || [];
  draftQueue = draftQueue.filter(track => library.some(saved => saved.id === track.id));
  const state = status.youtube;
  const activeYouTube = status.active?.kind === 'youtube';
  const source = document.querySelector('input[name=source]:checked').value;
  $('youtubeSettings').hidden = !activeYouTube && !['youtube', 'library'].includes(source);
  $('youtubeLink').hidden = source === 'library';
  $('youtubePrevious').disabled = !activeYouTube || !state?.queue.length;
  $('youtubeNext').disabled = !activeYouTube || !state?.queue.length;
  $('playerStop').disabled = !activeYouTube;
  $('youtubeSettings').classList.toggle('is-playing', activeYouTube && state?.status === 'playing');
  if (activeYouTube) $('youtubeLoop').value = state.loop;
  const phases = {loading:'Chargement de la playlist…', downloading:'Téléchargement du son…', playing:'Lecture en cours', stopped:'Lecture terminée', error:'Lecture interrompue'};
  const preview = !activeYouTube && source === 'library';
  const queue = preview ? draftQueue : (state?.queue || []);
  $('playerBadge').textContent = activeYouTube ? phases[state.status] : 'Prêt à diffuser';
  $('playerTitle').textContent = activeYouTube ? state.title : (queue[0]?.title || 'Choisissez votre musique');
  const seconds = Math.floor(state?.elapsed || 0);
  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  $('playerSubtitle').textContent = activeYouTube ? `Piste ${state.index + 1} sur ${state.queue.length} · ${clock} · zones ${status.active.zones.join(', ')}` : `${queue.length} piste(s) dans la file`;
  const position = state?.queue.length ? ` · ${state.index + 1}/${state.queue.length}` : '';
  $('youtubeStatus').textContent = state ? `${phases[state.status] || state.status}${position}${state.error ? ' · ' + state.error : ''}${state.warning && !state.error ? ' · ' + state.warning : ''}` : '';
  $('queueCount').textContent = `${queue.length} piste(s)`;
  $('queueEmpty').hidden = queue.length > 0;
  const signature = JSON.stringify([queue, activeYouTube ? state.index : -1, activeYouTube, preview]);
  if (signature !== youtubeSignature && !draggedTrack && !ordering) {
    youtubeSignature = signature;
    $('youtubeQueue').replaceChildren();
    queue.forEach((track, index) => {
      const item = document.createElement('li');
      const label = document.createElement('span'); label.textContent = track.title;
      label.className = 'queue-title';
      if (activeYouTube && index === state.index) item.setAttribute('aria-current', 'true');
      item.append(label);
      const editable = activeYouTube || preview;
      item.draggable = editable;
      item.ondragstart = event => {
        draggedTrack = track.queue_id;
        event.dataTransfer.setData('text/plain', track.queue_id);
        event.dataTransfer.effectAllowed = 'move';
      };
      item.ondragover = event => { if (editable && draggedTrack) event.preventDefault(); };
      item.ondrop = event => {
        event.preventDefault();
        const from = queue.findIndex(t => t.queue_id === draggedTrack);
        draggedTrack = null;
        if (from >= 0) moveTrack(queue, from, index, activeYouTube);
      };
      item.ondragend = () => { draggedTrack = null; renderYouTube(musicStatus); };
      const actions = document.createElement('div'); actions.className = 'queue-actions';
      for (const [symbol, offset, title] of [['↑', -1, 'Monter'], ['↓', 1, 'Descendre']]) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary';
        button.textContent = symbol; button.setAttribute('aria-label', `${title} ${track.title}`);
        button.disabled = !editable || index + offset < 0 || index + offset >= queue.length;
        button.onclick = () => moveTrack(queue, index, index + offset, activeYouTube);
        actions.append(button);
      }
      if (preview) {
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary'; remove.textContent = '×';
        remove.setAttribute('aria-label', `Retirer ${track.title} de la file`);
        remove.onclick = () => { draftQueue = draftQueue.filter(t => t.queue_id !== track.queue_id); renderYouTube(musicStatus); };
        actions.append(remove);
      }
      item.append(actions);
      $('youtubeQueue').append(item);
    });
  }
  $('libraryCount').textContent = `${library.length} piste(s)`;
  $('libraryEmpty').hidden = library.length > 0;
  const savedSignature = JSON.stringify([library, !!status.active, draftQueue.map(t => t.id)]);
  if (savedSignature !== librarySignature) {
    librarySignature = savedSignature;
    $('audioLibrary').replaceChildren();
    for (const track of library) {
      const row = document.createElement('div'); row.className = 'library-track';
      const label = document.createElement('span'); label.textContent = track.title;
      const add = document.createElement('button'); add.type = 'button'; add.className = 'secondary';
      const included = draftQueue.some(t => t.id === track.id);
      add.textContent = included ? 'Ajouté' : '+ Ajouter'; add.disabled = !!status.active || included;
      add.setAttribute('aria-label', `Ajouter ${track.title} à la file`);
      add.onclick = () => {
        draftQueue.push({...track, queue_id:track.id});
        const radio = document.querySelector('input[name=source][value=library]'); radio.checked = true; radio.onchange();
        renderYouTube(musicStatus);
      };
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'library-delete';
      remove.textContent = 'Supprimer'; remove.disabled = !!status.active;
      remove.setAttribute('aria-label', `Supprimer ${track.title} de la bibliothèque`);
      remove.onclick = async () => {
        if (!confirm(`Supprimer définitivement « ${track.title} » du serveur ?`)) return;
        try { await api(`library/${track.id}`, {}, 'DELETE'); draftQueue = draftQueue.filter(t => t.id !== track.id); }
        catch (error) { message(error.message); }
        await refresh();
      };
      row.append(label, add, remove); $('audioLibrary').append(row);
    }
  }
}
async function moveTrack(queue, from, to, activeYouTube) {
  if (ordering || from === to) return;
  const reordered = [...queue]; reordered.splice(to, 0, reordered.splice(from, 1)[0]);
  ordering = true;
  try {
    if (activeYouTube) await api('youtube/order', {order:reordered.map(t => t.queue_id)});
    else draftQueue = reordered;
    message('');
  } catch (error) { message(error.message); }
  finally { ordering = false; await refresh(); }
}
$('playerStop').onclick = async () => {
  try { await api('stop', {}); await cleanup(); await refresh(); }
  catch (error) { message(error.message); }
};
for (const [button, action] of [['youtubePrevious', 'previous'], ['youtubeNext', 'next']]) {
  $(button).onclick = async () => {
    $(button).disabled = true;
    try { await api('youtube/control', {action}); message(''); }
    catch (error) { message(error.message); }
    finally { await refresh(); }
  };
}
$('youtubeLoop').onchange = async () => {
  if (musicStatus?.active?.kind !== 'youtube') return;
  try { await api('youtube/control', {action:'loop', loop:$('youtubeLoop').value}); }
  catch (error) { message(error.message); }
  finally { await refresh(); }
};
$('loginForm').onsubmit = async event => {
  event.preventDefault();
  try { await api('login', {password:$('password').value}); $('password').value=''; message(''); await refresh(); }
  catch (error) { message(error.message); }
};
$('settingsButton').onclick = () => { $('settingsDialog').showModal(); refresh(); };
$('closeSettings').onclick = () => $('settingsDialog').close();
function resetZoneForm() {
  editingZone = null; $('zoneForm').reset();
  $('zoneFormTitle').textContent = 'Ajouter une zone';
  $('saveZone').textContent = 'Ajouter la zone'; $('cancelZone').hidden = true;
}
function renderZoneList() {
  $('zoneList').replaceChildren();
  for (const zone of currentZones) {
    const row = document.createElement('div'); row.className = 'zone-row';
    const label = document.createElement('span'); label.textContent = `${zone.id} · ${zone.name}`;
    const actions = document.createElement('div'); actions.className = 'zone-actions';
    const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'secondary'; edit.textContent = 'Modifier';
    edit.setAttribute('aria-label', `Modifier la zone ${zone.name}`);
    edit.onclick = () => {
      editingZone = zone.id; $('zoneNumber').value = zone.id; $('zoneName').value = zone.name;
      $('zoneFormTitle').textContent = 'Modifier la zone'; $('saveZone').textContent = 'Enregistrer';
      $('cancelZone').hidden = false; $('zoneStatus').textContent = ''; $('zoneName').focus();
    };
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'danger'; remove.textContent = 'Supprimer';
    remove.setAttribute('aria-label', `Supprimer la zone ${zone.name}`);
    remove.onclick = async () => {
      remove.disabled = true;
      try {
        await api(`settings/zones/${zone.id}`, {}, 'DELETE');
        if (editingZone === zone.id) resetZoneForm();
        $('zoneStatus').textContent = 'Zone supprimée.'; await refresh();
      } catch (error) { $('zoneStatus').textContent = error.message; remove.disabled = settingsActive; }
    };
    actions.append(edit, remove); row.append(label, actions); $('zoneList').append(row);
  }
}
$('cancelZone').onclick = resetZoneForm;
$('zoneForm').onsubmit = async event => {
  event.preventDefault(); $('saveZone').disabled = true;
  try {
    const body = {id:Number($('zoneNumber').value), name:$('zoneName').value.trim()};
    await api(editingZone === null ? 'settings/zones' : `settings/zones/${editingZone}`, body, editingZone === null ? 'POST' : 'PUT');
    $('zoneStatus').textContent = editingZone === null ? 'Zone ajoutée.' : 'Zone modifiée.';
    resetZoneForm(); await refresh();
  } catch (error) { $('zoneStatus').textContent = error.message; }
  finally { $('saveZone').disabled = false; }
};
$('multicast').oninput = () => { networkDirty = true; $('networkStatus').textContent = ''; };
$('networkForm').onsubmit = async event => {
  event.preventDefault();
  $('saveNetwork').disabled = true;
  try {
    const result = await api('settings/network', {multicast_address:$('multicast').value.trim()});
    $('multicast').value = result.multicast_address; networkDirty = false;
    $('networkStatus').textContent = 'Adresse multicast enregistrée : ' + result.multicast_address;
  } catch (error) { $('networkStatus').textContent = error.message; }
  finally { await refresh(); }
};
$('all').onclick = () => {
  const boxes = [...$('zones').querySelectorAll('input')];
  const value = !boxes.every(box => box.checked); boxes.forEach(box => box.checked = value);
};
document.querySelectorAll('input[name=source]').forEach(input => input.onchange = () => {
  const source = document.querySelector('input[name=source]:checked').value;
  const file = source === 'file';
  $('file').hidden = !file; $('audio').hidden = !file;
  $('loopbackSettings').hidden = source !== 'loopback';
  $('youtubeSettings').hidden = !['youtube', 'library'].includes(source);
  if (musicStatus) renderYouTube(musicStatus);
});
$('listAudioInputs').onclick = async () => {
  if (busy || running) return;
  $('listAudioInputs').disabled = true;
  let permissionStream;
  try {
    if (!window.isSecureContext) throw new Error('Utilisez HTTPS ou déclarez cette adresse fiable dans Firefox avant de rechercher les entrées.');
    permissionStream = await navigator.mediaDevices.getUserMedia({audio:true});
    const inputs = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput' && device.deviceId);
    const previous = $('audioInput').value;
    $('audioInput').replaceChildren(new Option('Choisissez Mixage stéréo ou une entrée virtuelle', ''));
    inputs.forEach((device, index) => $('audioInput').append(new Option(device.label || `Entrée audio ${index + 1}`, device.deviceId)));
    if (inputs.some(device => device.deviceId === previous)) $('audioInput').value = previous;
    $('audioInputStatus').textContent = 'Choisissez explicitement l’entrée de bouclage. Si elle est absente, activez-la dans Windows puis recherchez à nouveau. Un microphone capte la pièce, pas le son de YouTube.';
  } catch (error) {
    $('audioInputStatus').textContent = error.message;
  } finally {
    permissionStream?.getTracks().forEach(track => track.stop());
    $('listAudioInputs').disabled = false;
  }
};
$('file').onchange = () => {
  if (objectURL) URL.revokeObjectURL(objectURL);
  const file = $('file').files[0];
  if (file) { objectURL = URL.createObjectURL(file); $('audio').src = objectURL; }
};
$('start').onclick = async () => {
  if (busy || running) return;
  busy = true; $('start').disabled = true; message('');
  try {
    if (networkDirty) throw new Error('Enregistrez l’adresse multicast avant de démarrer la diffusion.');
    const zones = [...$('zones').querySelectorAll('input:checked')].map(input => Number(input.value));
    if (!zones.length) throw new Error('Sélectionnez au moins une zone.');
    const source = document.querySelector('input[name=source]:checked').value;
    if (source === 'library') {
      if (!draftQueue.length) throw new Error('Ajoutez au moins une piste de la bibliothèque à la file.');
      await api('library/start', {tracks:draftQueue.map(track => track.id), zones, loop:$('youtubeLoop').value});
      busy = false;
      await refresh();
      return;
    }
    if (source === 'youtube') {
      const url = $('youtubeUrl').value.trim();
      if (!url) throw new Error('Saisissez le lien d’une vidéo YouTube ou d’une playlist.');
      await api('youtube/start', {url, zones, loop:$('youtubeLoop').value});
      busy = false;
      await refresh();
      return;
    }
    if (!window.isSecureContext) throw new Error('Pour capturer le son, utilisez HTTPS avec un certificat approuvé ou http://127.0.0.1 sur le PC hébergeant l’application.');
    context = new AudioContext({sampleRate:48000, latencyHint:'interactive'});
    await context.resume();
    let node;
    if (source === 'file') {
      if (!$('file').files.length) throw new Error('Choisissez un fichier audio.');
      // A fresh element permits a new MediaElementSource on each session.
      const previous = $('audio');
      const audio = previous.cloneNode(); previous.replaceWith(audio);
      node = context.createMediaElementSource(audio);
      audio.onended = () => cleanup();
    } else {
      if (source === 'loopback' && !$('audioInput').value) throw new Error('Recherchez les entrées audio et choisissez Mixage stéréo ou une entrée virtuelle.');
      stream = source === 'mic' || source === 'loopback'
        ? await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false,
            ...(source === 'loopback' ? {deviceId:{exact:$('audioInput').value}} : {})}})
        : await navigator.mediaDevices.getDisplayMedia({video:true,audio:true,systemAudio:'include'});
      if (!stream.getAudioTracks().length) throw new Error('Le navigateur n’a fourni aucune piste audio. Pour YouTube, utilisez Chrome ou Edge, choisissez « Onglet » puis cochez « Partager l’audio de l’onglet ». Firefox ne prend pas en charge cette capture audio ; utilisez un fichier audio ou le microphone.');
      stream.getTracks().forEach(track => track.onended = () => cleanup());
      node = context.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
    }
    await context.audioWorklet.addModule('/static/pcm.js');
    processor = new AudioWorkletNode(context, 'pcm');
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/live`);
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Le serveur ne répond pas.')), 10000);
      socket.onopen = () => socket.send(JSON.stringify({zones,source:$('pc').value || 'Mon PC'}));
      socket.onerror = () => { clearTimeout(timeout); reject(new Error('Connexion audio impossible.')); };
      socket.onclose = () => { clearTimeout(timeout); reject(new Error('Connexion refusée.')); };
      socket.onmessage = event => {
        const data = JSON.parse(event.data); clearTimeout(timeout);
        if (data.error) reject(new Error(data.error)); else if (data.ready) resolve();
      };
    });
    running = true;
    socket.onmessage = event => { const data = JSON.parse(event.data); if (data.error) message(data.error); };
    socket.onclose = () => { cleanup(); refresh(); };
    processor.port.onmessage = event => {
      if (socket?.readyState === WebSocket.OPEN) {
        if (socket.bufferedAmount > 192000) { message('Réseau trop lent : diffusion arrêtée.'); cleanup(); }
        else socket.send(event.data);
      }
    };
    node.connect(processor); processor.connect(context.destination);
    if (source === 'file') await $('audio').play();
    await refresh();
  } catch (error) { message(error.message); await cleanup(); }
};
$('stop').onclick = async () => { try { await api('stop', {}); await cleanup(); await refresh(); } catch (error) { message(error.message); } };
$('logout').onclick = async () => { await cleanup(); await api('logout', {}); await refresh(); };
setInterval(refresh, 2000); refresh();
