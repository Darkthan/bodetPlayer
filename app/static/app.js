const $ = id => document.getElementById(id);
let socket, context, stream, processor, running = false, busy = false;
let networkDirty = false;
let audioDirty = false;
let agentsSignature = '';
let youtubeSignature = '';
let librarySignature = '', draftQueue = [], musicStatus = null, draggedTrack = null, ordering = false;
let selectedSounds = new Set(), playlistsSignature = '', selectedPlaylist = '';
let queueSequence = 0;
const nextQueueId = () => `draft-${++queueSequence}`;
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
    $('audioFields').disabled = !!status.active;
    if (!audioDirty && status.audio) {
      $('audioBlock').value = status.audio.block_ms;
      $('audioBacklog').value = status.audio.max_backlog_ms;
    }
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
    $('start').disabled = !!status.active || busy || uploading;
    $('playYoutube').disabled = !!status.active || busy || uploading;
    $('file').disabled = !!status.active || uploading;
    renderAgents(status);
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
  busy = false; $('start').disabled = false;
}
function renderYouTube(status) {
  musicStatus = status;
  const library = status.library || [];
  selectedSounds = new Set([...selectedSounds].filter(id => library.some(track => track.id === id)));
  renderPlaylists(status);
  draftQueue = draftQueue.filter(track => library.some(saved => saved.id === track.id));
  const state = status.youtube;
  const activeYouTube = status.active?.kind === 'youtube';
  const source = document.querySelector('input[name=source]:checked').value;
  $('youtubeSettings').hidden = !activeYouTube && source !== 'library';

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
  const savedSignature = JSON.stringify([library, !!status.active, draftQueue.map(t => t.id), [...selectedSounds]]);
  if (savedSignature !== librarySignature) {
    librarySignature = savedSignature;
    $('audioLibrary').replaceChildren();
    for (const track of library) {
      const row = document.createElement('div'); row.className = 'library-track';
      const select = document.createElement('input'); select.type = 'checkbox';
      select.checked = selectedSounds.has(track.id); select.disabled = !!status.active;
      select.setAttribute('aria-label', `Sélectionner ${track.title}`);
      select.onchange = () => {
        if (select.checked) selectedSounds.add(track.id); else selectedSounds.delete(track.id);
        renderYouTube(musicStatus);
      };
      const label = document.createElement('span'); label.textContent = track.title;
      const add = document.createElement('button'); add.type = 'button'; add.className = 'secondary';
      const included = draftQueue.some(t => t.id === track.id);
      add.textContent = included ? 'Ajouté' : '+ Ajouter'; add.disabled = !!status.active || included;
      add.setAttribute('aria-label', `Ajouter ${track.title} à la file`);
      add.onclick = () => {
        if (draftQueue.length >= 100) { message('Une playlist contient au maximum 100 pistes.'); return; }
        draftQueue.push({...track, queue_id:nextQueueId()});
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
      row.append(select, label, add, remove); $('audioLibrary').append(row);
    }
  }
  $('selectionCount').textContent = `${selectedSounds.size} son(s) sélectionné(s)`;
  $('selectAllSounds').disabled = !!status.active || !library.length;
  $('selectAllSounds').textContent = selectedSounds.size === library.length && library.length ? 'Tout désélectionner' : 'Tout sélectionner';
  $('addSelectedSounds').disabled = !!status.active || !selectedSounds.size;
  $('deleteSelectedSounds').disabled = !!status.active || !selectedSounds.size;
}
function renderAgents(status) {
  const agents = status.agents || [];
  const signature = JSON.stringify(agents);
  if (signature !== agentsSignature) {
    agentsSignature = signature;
    const previous = $('windowsAgent').value;
    $('windowsAgent').replaceChildren(new Option('Choisissez un PC Windows connecté', ''));
    agents.forEach(agent => $('windowsAgent').append(new Option(`${agent.name} — ${agent.online ? 'connecté' : 'hors ligne'}`, agent.id)));
    if (agents.some(agent => agent.id === previous)) $('windowsAgent').value = previous;
    else if (agents.filter(agent => agent.online).length === 1) $('windowsAgent').value = agents.find(agent => agent.online).id;
    $('agentList').replaceChildren();
    agents.forEach(agent => {
      const row = document.createElement('div'); row.className = 'zone-row';
      const label = document.createElement('span');
      label.textContent = `${agent.name} · ${agent.online ? (agent.status === 'capturing' ? 'Capture en cours' : 'Connecté') : 'Hors ligne'}${agent.error ? ' · ' + agent.error : ''}`;
      const revoke = document.createElement('button'); revoke.type = 'button'; revoke.className = 'secondary';
      revoke.textContent = 'Retirer'; revoke.setAttribute('aria-label', `Retirer l’agent ${agent.name}`);
      revoke.onclick = async () => {
        if (!confirm(`Retirer l’agent « ${agent.name} » et arrêter sa capture éventuelle ?`)) return;
        try { await api(`agents/${agent.id}`, {}, 'DELETE'); message('Agent retiré.'); }
        catch (error) { message(error.message); }
        await refresh();
      };
      row.append(label, revoke); $('agentList').append(row);
    });
  }
  $('windowsAgent').disabled = !!status.active;
  const source = document.querySelector('input[name=source]:checked').value;
  $('agentSettings').hidden = source !== 'agent' && status.active?.kind !== 'agent';
}
$('pairWindowsAgent').onclick = async () => {
  try {
    const result = await api('agents/pairing', {});
    $('agentPairCode').textContent = `Adresse du serveur : ${location.origin} · Code : ${result.code} · Valable 5 minutes, utilisable une seule fois.`;
  } catch (error) { message(error.message); }
};
function renderPlaylists(status) {
  const playlists = status.playlists || [];
  if (selectedPlaylist && !playlists.some(p => p.id === selectedPlaylist)) {
    selectedPlaylist = ''; $('playlistName').value = '';
  }
  const signature = JSON.stringify(playlists);
  if (signature !== playlistsSignature) {
    playlistsSignature = signature;
    $('savedPlaylist').replaceChildren(new Option('Nouvelle playlist', ''));
    playlists.forEach(playlist => $('savedPlaylist').append(new Option(`${playlist.name} · ${playlist.tracks.length} piste(s)`, playlist.id)));
    $('savedPlaylist').value = selectedPlaylist;
  }
  for (const id of ['savedPlaylist', 'playlistName', 'newPlaylist', 'savePlaylist']) $(id).disabled = !!status.active;
  $('deletePlaylist').disabled = !!status.active || !selectedPlaylist;
  $('savePlaylist').textContent = selectedPlaylist ? 'Enregistrer les modifications' : 'Enregistrer la playlist';
}
$('savedPlaylist').onchange = () => {
  selectedPlaylist = $('savedPlaylist').value;
  const playlist = musicStatus.playlists?.find(p => p.id === selectedPlaylist);
  $('playlistName').value = playlist?.name || '';
  const library = musicStatus.library || [];
  draftQueue = (playlist?.tracks || []).map(id => library.find(track => track.id === id))
    .filter(Boolean).map(track => ({...track, queue_id:nextQueueId()}));
  renderYouTube(musicStatus);
};
$('newPlaylist').onclick = () => {
  selectedPlaylist = ''; $('savedPlaylist').value = ''; $('playlistName').value = '';
  draftQueue = []; renderYouTube(musicStatus); $('playlistName').focus();
};
$('savePlaylist').onclick = async () => {
  const name = $('playlistName').value.trim();
  if (!name) { message('Donnez un nom à la playlist.'); $('playlistName').focus(); return; }
  try {
    const result = await api(selectedPlaylist ? `playlists/${selectedPlaylist}` : 'playlists',
      {name, tracks:draftQueue.map(track => track.id)}, selectedPlaylist ? 'PUT' : 'POST');
    selectedPlaylist = result.id; playlistsSignature = ''; message('Playlist enregistrée.');
  } catch (error) { message(error.message); }
  await refresh();
};
$('deletePlaylist').onclick = async () => {
  if (!selectedPlaylist || !confirm('Supprimer cette playlist ? Les sons resteront dans la bibliothèque.')) return;
  try {
    await api(`playlists/${selectedPlaylist}`, {}, 'DELETE');
    selectedPlaylist = ''; $('playlistName').value = ''; draftQueue = [];
    message('Playlist supprimée. Les sons sont conservés.');
  } catch (error) { message(error.message); }
  await refresh();
};
$('selectAllSounds').onclick = () => {
  const library = musicStatus.library || [];
  selectedSounds = selectedSounds.size === library.length ? new Set() : new Set(library.map(track => track.id));
  renderYouTube(musicStatus);
};
$('addSelectedSounds').onclick = () => {
  const tracks = musicStatus.library.filter(track => selectedSounds.has(track.id) && !draftQueue.some(t => t.id === track.id));
  if (draftQueue.length + tracks.length > 100) { message('Une playlist contient au maximum 100 pistes.'); return; }
  draftQueue.push(...tracks.map(track => ({...track, queue_id:nextQueueId()})));
  selectedSounds.clear(); renderYouTube(musicStatus);
};
$('deleteSelectedSounds').onclick = async () => {
  const tracks = [...selectedSounds];
  if (!tracks.length || !confirm(`Supprimer définitivement ${tracks.length} son(s) du serveur et de toutes les playlists ?`)) return;
  $('deleteSelectedSounds').disabled = true;
  try {
    await api('library/delete', {tracks}); selectedSounds.clear();
    message(`${tracks.length} son(s) supprimé(s).`);
  } catch (error) { message(error.message); }
  await refresh();
};
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
for (const id of ['audioBlock', 'audioBacklog']) $(id).oninput = () => {
  audioDirty = true; $('audioStatus').textContent = '';
};
for (const [id, block, backlog] of [['audioFast', 10, 100], ['audioBalanced', 20, 200], ['audioStable', 40, 500]]) {
  $(id).onclick = () => {
    $('audioBlock').value = block; $('audioBacklog').value = backlog;
    audioDirty = true; $('audioStatus').textContent = 'Cliquez sur Enregistrer pour appliquer ce profil.';
  };
}
$('audioForm').onsubmit = async event => {
  event.preventDefault(); $('saveAudio').disabled = true;
  try {
    await api('settings/audio', {block_ms:Number($('audioBlock').value), max_backlog_ms:Number($('audioBacklog').value)});
    audioDirty = false; $('audioStatus').textContent = 'Réglages enregistrés pour la prochaine diffusion.';
  } catch (error) { $('audioStatus').textContent = error.message; }
  finally { $('saveAudio').disabled = false; await refresh(); }
};
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
  $('youtubeSettings').hidden = source !== 'library';
  $('captureHint').hidden = source === 'library';
  $('agentSettings').hidden = source !== 'agent';
  if (source === 'agent') $('captureHint').hidden = true;
  $('captureHint').textContent = source === 'mic'
    ? 'Autorisez le microphone pour diffuser votre voix.'
    : 'Dans Chrome ou Edge, choisissez un onglet et cochez « Partager l’audio de l’onglet ». Firefox ne permet pas cette capture ; utilisez la playlist pour YouTube.';
  if (musicStatus) renderYouTube(musicStatus);
});
let uploading = false;
async function uploadAudioFiles(files) {
  if (uploading) return;
  if (busy || running || musicStatus?.active) { message('Arrêtez la diffusion avant d’importer des sons.'); return; }
  if (!files.length) return;
  uploading = true; $('file').disabled = true; $('start').disabled = true; $('playYoutube').disabled = true;
  const outcomes = [];
  try {
    for (const file of files) {
      if (file.size > 100 * 1024 * 1024) { outcomes.push(`${file.name} : dépasse 100 Mio.`); continue; }
      $('uploadStatus').textContent = `Import de ${file.name}…`;
      try {
        const response = await fetch('/api/library/upload?name=' + encodeURIComponent(file.name), {
          method:'POST', headers:{'Content-Type':'application/octet-stream'}, body:file
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Import impossible.');
        draftQueue.push({...result, queue_id:result.id});
        outcomes.push(`${file.name} : ajouté à la playlist.`);
      } catch (error) { outcomes.push(`${file.name} : ${error.message}`); }
    }
    await refresh();
  } finally {
    uploading = false; $('file').disabled = false; $('file').value = '';
    $('uploadStatus').textContent = outcomes.join(' ');
    await refresh();
  }
}
$('file').onchange = () => uploadAudioFiles(Array.from($('file').files));
const isFileDrop = event => Array.from(event.dataTransfer?.types || []).includes('Files');
document.addEventListener('dragover', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault(); event.dataTransfer.dropEffect = $('dashboard').hidden ? 'none' : 'copy';
  if (!$('youtubeSettings').hidden) $('fileDrop').classList.add('drag-over');
});
document.addEventListener('dragleave', event => {
  if (!event.relatedTarget) $('fileDrop').classList.remove('drag-over');
});
document.addEventListener('drop', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault(); $('fileDrop').classList.remove('drag-over');
  if ($('dashboard').hidden) { message('Connectez-vous avant de déposer un son.'); return; }
  const radio = document.querySelector('input[name=source][value=library]'); radio.checked = true; radio.onchange();
  uploadAudioFiles(Array.from(event.dataTransfer.files));
});
$('playYoutube').onclick = async () => {
  if (busy || running || uploading) return;
  busy = true; $('playYoutube').disabled = true;
  try {
    if (networkDirty) throw new Error('Enregistrez l’adresse multicast avant de démarrer.');
    const zones = [...$('zones').querySelectorAll('input:checked')].map(input => Number(input.value));
    if (!zones.length) throw new Error('Sélectionnez au moins une zone.');
    const url = $('youtubeUrl').value.trim();
    if (!url) throw new Error('Saisissez un lien YouTube.');
    await api('youtube/start', {url, zones, loop:$('youtubeLoop').value});
    message('');
  } catch (error) { message(error.message); }
  finally { busy = false; $('playYoutube').disabled = false; await refresh(); }
};
$('start').onclick = async () => {
  if (busy || running || uploading) return;
  busy = true; $('start').disabled = true; message('');
  try {
    if (networkDirty) throw new Error('Enregistrez l’adresse multicast avant de démarrer la diffusion.');
    const zones = [...$('zones').querySelectorAll('input:checked')].map(input => Number(input.value));
    if (!zones.length) throw new Error('Sélectionnez au moins une zone.');
    const source = document.querySelector('input[name=source]:checked').value;
    if (source === 'agent') {
      if (!$('windowsAgent').value) throw new Error('Choisissez un agent Windows connecté.');
      await api(`agents/${$('windowsAgent').value}/start`, {zones});
      busy = false; await refresh(); return;
    }
    if (source === 'library') {
      if (!draftQueue.length) throw new Error('Ajoutez au moins une piste de la bibliothèque à la file.');
      await api('library/start', {tracks:draftQueue.map(track => track.id), zones, loop:$('youtubeLoop').value});
      busy = false;
      await refresh();
      return;
    }
    if (!window.isSecureContext) throw new Error('Pour capturer le son, utilisez HTTPS avec un certificat approuvé ou http://127.0.0.1 sur le PC hébergeant l’application.');
    context = new AudioContext({sampleRate:48000, latencyHint:'interactive'});
    await context.resume();
    let node;
    stream = source === 'mic'
      ? await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}})
      : await navigator.mediaDevices.getDisplayMedia({video:true,audio:true,systemAudio:'include'});
    if (!stream.getAudioTracks().length) throw new Error('Aucun son partagé. Dans Chrome ou Edge, choisissez un onglet et cochez « Partager l’audio ». Pour YouTube dans Firefox, utilisez le lien dans Playlist.');
    stream.getTracks().forEach(track => track.onended = () => cleanup());
    node = context.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
    await context.audioWorklet.addModule('/static/pcm.js');
    const captureSettings = musicStatus?.audio || {block_ms:20, max_backlog_ms:200};
    processor = new AudioWorkletNode(context, 'pcm', {processorOptions:{blockFrames:captureSettings.block_ms * 48}});
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
        if (socket.bufferedAmount + event.data.byteLength > captureSettings.max_backlog_ms * 96) {
          message(`Plus de ${captureSettings.max_backlog_ms} ms de son en attente : diffusion arrêtée. Augmentez la limite de buffer si le réseau est instable.`); cleanup();
        }
        else socket.send(event.data);
      }
    };
    node.connect(processor); processor.connect(context.destination);
    await refresh();
  } catch (error) { message(error.message); await cleanup(); }
};
$('stop').onclick = async () => { try { await api('stop', {}); await cleanup(); await refresh(); } catch (error) { message(error.message); } };
$('logout').onclick = async () => { await cleanup(); await api('logout', {}); await refresh(); };
setInterval(refresh, 2000); refresh();
