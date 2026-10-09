const $ = id => document.getElementById(id);
let busy = false, networkDirty = false, audioDirty = false;
let localAgentId = '', localAgentChecked = 0, localAgentLookup = null;
let currentStatus = null, zonesSignature = '', currentZones = [], editingZone = null, settingsActive = false;
const message = text => { $('message').textContent = text; };
async function api(path, body, method = 'POST') {
  const response = await fetch('/api/' + path, body === undefined ? {} : {
    method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Erreur du serveur');
  return result;
}
async function discoverLocalAgent(force = false) {
  if (localAgentLookup) return localAgentLookup;
  if (!force && Date.now() - localAgentChecked < 10000) return localAgentId;
  localAgentLookup = (async () => {
    $('agentPairCode').textContent = 'Recherche de l’agent sur ce PC… Si Firefox demande l’accès aux applications et services de cet appareil, cliquez sur Autoriser.';
    try {
      const challenge = await api('agents/local/challenge', {});
      const response = await fetch(`http://127.0.0.1:17861/identity?challenge=${challenge.challenge}`, {
        credentials:'omit', targetAddressSpace:'loopback', signal:AbortSignal.timeout(30000)
      });
      if (!response.ok) throw new Error(`L’agent local a refusé la vérification (${response.status}). Vérifiez que son adresse du serveur correspond à celle de cette page.`);
      const identity = await api('agents/local', await response.json());
      localAgentId = identity.id;
      $('agentPairCode').textContent = 'Agent de ce PC détecté. Le contrôle est limité à ce PC.';
    } catch (error) {
      localAgentId = '';
      $('agentPairCode').textContent = error.name === 'TimeoutError' || error.name === 'AbortError'
        ? 'La détection a expiré. Autorisez ce site à accéder aux applications et services de cet appareil dans Firefox, puis cliquez sur Vérifier l’agent.'
        : error instanceof TypeError
          ? 'Impossible de joindre l’agent local. Vérifiez son icône près de l’horloge Windows et son état, puis autorisez l’accès aux applications et services de cet appareil dans Firefox et cliquez sur Vérifier l’agent.'
          : `Vérification locale : ${error.message}`;
    } finally { localAgentChecked = Date.now(); localAgentLookup = null; }
    return localAgentId;
  })();
  return localAgentLookup;
}
async function refresh() {
  try {
    const status = await api('status');
    currentStatus = status;
    $('login').hidden = true; $('dashboard').hidden = false; $('settingsButton').hidden = false;
    settingsActive = !!status.active;
    $('zoneFields').disabled = settingsActive; $('settingsBusy').hidden = !settingsActive;
    $('zoneList').querySelectorAll('button').forEach(button => button.disabled = settingsActive);
    if (!networkDirty) $('multicast').value = status.multicast_address;
    $('multicast').disabled = settingsActive; $('saveNetwork').disabled = settingsActive;
    $('audioFields').disabled = settingsActive;
    if (!audioDirty && status.audio) {
      $('audioBlock').value = status.audio.block_ms; $('audioBacklog').value = status.audio.max_backlog_ms;
    }
    $('mode').hidden = status.mode !== 'simulation';
    $('mode').textContent = 'Mode simulation : le son arrive au serveur sans être envoyé aux enceintes.';
    const signature = JSON.stringify(status.zones);
    if (signature !== zonesSignature) {
      const selected = new Set([...$('zones').querySelectorAll('input:checked')].map(input => Number(input.value)));
      currentZones = status.zones; zonesSignature = signature; $('zones').replaceChildren();
      for (const zone of status.zones) {
        const label = document.createElement('label');
        const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = zone.id;
        checkbox.checked = selected.has(zone.id);
        label.append(checkbox, document.createTextNode(' ' + zone.name + ' · ' + zone.id)); $('zones').append(label);
      }
      if (!status.zones.length) $('zones').textContent = 'Aucune zone configurée. Ajoutez-en dans les paramètres.';
      renderZoneList();
    }
    $('zoneList').querySelectorAll('button').forEach(button => button.disabled = settingsActive);
    renderAgent(status);
    await discoverLocalAgent();
    renderAgent(currentStatus);
  } catch (error) {
    if (!currentStatus || error.message === 'Connectez-vous.') {
      currentStatus = null; localAgentId = ''; localAgentChecked = 0;
      $('login').hidden = false; $('dashboard').hidden = true; $('settingsButton').hidden = true;
      $('settingsDialog').close(); $('diffusionDialog').close();
    } else message(error.message);
  }
}
function renderAgent(status) {
  const agent = (status.agents || []).find(item => item.id === localAgentId);
  const ownCapture = status.active?.kind === 'agent' && status.active.agent_id === localAgentId;
  $('agentSettings').hidden = !!agent;
  $('agentName').textContent = agent?.name || 'Son de votre PC';
  $('agentState').textContent = !agent ? 'Agent non détecté' : !agent.online ? 'Connexion au serveur en attente' : ownCapture ? 'Diffusion en cours' : 'Prêt à diffuser';
  $('agentState').classList.toggle('is-live', !!ownCapture);
  $('manageDiffusion').disabled = !agent?.online;
  $('start').disabled = !!status.active || busy || !agent?.online;
  $('stop').disabled = !ownCapture || busy;
  $('activity').textContent = ownCapture
    ? 'Diffusion vers les zones ' + status.active.zones.join(', ') + '.'
    : status.active ? 'Une autre diffusion est en cours.' : 'Aucune diffusion en cours.';
  $('diffusionState').textContent = agent?.error || $('activity').textContent;
}
function clearZoneWarning() {
  if ($('zones').querySelector('input:checked')) {
    $('zoneWarning').hidden = true; $('zonesSection').classList.remove('needs-selection');
  }
}
$('zones').onchange = clearZoneWarning;
$('manageDiffusion').onclick = () => { $('diffusionDialog').showModal(); refresh(); };
$('closeDiffusion').onclick = () => $('diffusionDialog').close();
document.querySelector('.download-agent').href = '/api/agents/download?server=' + encodeURIComponent(location.origin);
$('checkLocalAgent').onclick = async () => {
  $('checkLocalAgent').disabled = true;
  try { await discoverLocalAgent(true); await refresh(); }
  finally { $('checkLocalAgent').disabled = false; }
};
$('start').onclick = async () => {
  $('diffusionError').hidden = true;
  const zones = [...$('zones').querySelectorAll('input:checked')].map(input => Number(input.value));
  if (!zones.length) {
    $('zoneWarning').hidden = false; $('zonesSection').classList.add('needs-selection');
    $('zoneWarning').focus(); $('zoneWarning').scrollIntoView({block:'nearest'});
    return;
  }
  if (busy) return;
  busy = true; renderAgent(currentStatus);
  try {
    if (networkDirty || audioDirty) throw new Error('Enregistrez les réglages dans les paramètres avant de diffuser.');
    const agentId = await discoverLocalAgent(true);
    if (!agentId) throw new Error('Aucun agent détecté sur ce PC. Vérifiez son icône près de l’horloge Windows.');
    await api('agents/' + agentId + '/start', {zones}); message('');
  } catch (error) { $('diffusionError').textContent = error.message; $('diffusionError').hidden = false; }
  finally { busy = false; await refresh(); }
};
$('stop').onclick = async () => {
  if (busy) return;
  busy = true; renderAgent(currentStatus);
  $('diffusionError').hidden = true;
  try { await discoverLocalAgent(true); await api('stop', {}); message(''); }
  catch (error) { $('diffusionError').textContent = error.message; $('diffusionError').hidden = false; }
  finally { busy = false; await refresh(); }
};
$('logout').onclick = async () => {
  try { await api('logout', {}); currentStatus = null; await refresh(); }
  catch (error) { message(error.message); }
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
  clearZoneWarning();
};

setInterval(refresh, 2000); refresh();
