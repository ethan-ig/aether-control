(() => {
  const homePanel = document.getElementById('homePanel');
  const homeGrid = document.getElementById('homeDeviceGrid');
  const homeBack = document.getElementById('homeBack');
  const homeRefresh = document.getElementById('homeRefresh');
  const homeTile = document.querySelector('[data-action="home"]');
  const homeSummary = document.getElementById('homeSummary');

  const updateControl = document.getElementById('updateControl');
  const updateStatusText = document.getElementById('updateStatusText');
  const updateStatusDot = document.getElementById('updateStatusDot');
  const updatePanel = document.getElementById('updatePanel');
  const updateClose = document.getElementById('updateClose');
  const updateVersion = document.getElementById('updateVersion');
  const updateSummary = document.getElementById('updateSummary');
  const updateCommits = document.getElementById('updateCommits');
  const updateInstall = document.getElementById('updateInstall');
  const updateRollback = document.getElementById('updateRollback');

  const ambientMode = document.getElementById('ambientMode');
  const ambientClock = document.getElementById('ambientClock');
  const ambientDate = document.getElementById('ambientDate');
  const ambientTemp = document.getElementById('ambientTemp');
  const ambientCondition = document.getElementById('ambientCondition');
  const ambientRack = document.getElementById('ambientRack');
  const ambientAether = document.getElementById('ambientAether');
  const ambientNetwork = document.getElementById('ambientNetwork');

  let homeDevices = [];
  let updateState = null;
  let ambientData = null;
  let idleTimer = null;
  let ambientPollTimer = null;
  const IDLE_MS = 120000;
  const WEATHER_CLASSES = [
    'weather-clear', 'weather-cloudy', 'weather-rain', 'weather-storm',
    'weather-snow', 'weather-fog', 'weather-wind', 'weather-neutral', 'weather-night'
  ];

  function featureToast(message, isError = false) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle('error', isError);
    toast.classList.add('show');
    clearTimeout(featureToast.timer);
    featureToast.timer = setTimeout(() => toast.classList.remove('show'), 3400);
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  }

  function weatherClassFor(condition) {
    const text = String(condition || '').toLowerCase();
    if (/thunder|storm|tornado|hail/.test(text)) return 'weather-storm';
    if (/snow|sleet|blizzard|freezing|ice/.test(text)) return 'weather-snow';
    if (/rain|shower|drizzle/.test(text)) return 'weather-rain';
    if (/fog|mist|haze|smoke/.test(text)) return 'weather-fog';
    if (/wind|breezy|gust/.test(text)) return 'weather-wind';
    if (/cloud|overcast/.test(text)) return 'weather-cloudy';
    if (/clear|sunny|fair/.test(text)) return 'weather-clear';
    return 'weather-neutral';
  }

  function applyWeatherTheme(weather) {
    WEATHER_CLASSES.forEach(cls => document.body.classList.remove(cls));
    document.body.classList.add(weatherClassFor(weather?.condition));
    const hour = new Date().getHours();
    if (hour >= 20 || hour < 6) document.body.classList.add('weather-night');
    document.body.dataset.weather = String(weather?.condition || 'weather').toLowerCase();
  }

  async function refreshWeatherTheme() {
    try {
      const res = await fetch('/api/weather', {cache:'no-store'});
      if (!res.ok) return;
      const weather = await res.json();
      applyWeatherTheme(weather);
    } catch (_) {}
  }

  async function loadHomeDevices() {
    if (!homeGrid) return;
    homeGrid.innerHTML = '<div class="home-loading">discovering Govee devices…</div>';
    try {
      const res = await fetch('/api/home', {cache:'no-store'});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      homeDevices = data.devices || [];
      if (homeSummary) homeSummary.textContent = `${homeDevices.length} devices`;
      if (!homeDevices.length) {
        homeGrid.innerHTML = '<div class="home-loading">no power-capable devices found</div>';
        return;
      }
      homeGrid.innerHTML = homeDevices.map((device, index) => {
        const stateClass = device.online === false ? 'offline' : device.power === 1 ? 'on' : 'off';
        const stateText = device.online === false ? 'offline' : device.power === 1 ? 'on' : 'off';
        return `<button class="home-device-card ${stateClass}" data-home-index="${index}">
          <div class="home-device-kind">${escapeHtml(device.category || device.sku || 'device')}</div>
          <div class="home-device-name">${escapeHtml(device.name)}</div>
          <div class="home-device-meta">
            <span>${escapeHtml(device.sku || '')}</span>
            <span class="home-device-state"><i></i>${escapeHtml(stateText)}</span>
          </div>
        </button>`;
      }).join('');
    } catch (err) {
      homeGrid.innerHTML = `<div class="home-loading">${escapeHtml(err.message || 'could not load devices')}</div>`;
      featureToast(`My Home: ${err.message}`, true);
    }
  }

  function openHome() {
    if (!homePanel) return;
    homePanel.classList.add('show');
    homePanel.setAttribute('aria-hidden', 'false');
    loadHomeDevices();
    resetIdleTimer();
  }

  function closeHome() {
    homePanel?.classList.remove('show');
    homePanel?.setAttribute('aria-hidden', 'true');
    resetIdleTimer();
  }

  homeTile?.addEventListener('click', openHome);
  homeBack?.addEventListener('click', closeHome);
  homeRefresh?.addEventListener('click', loadHomeDevices);

  homeGrid?.addEventListener('click', async event => {
    const card = event.target.closest('[data-home-index]');
    if (!card || card.classList.contains('busy')) return;
    const device = homeDevices[Number(card.dataset.homeIndex)];
    if (!device || device.online === false) {
      featureToast(`${device?.name || 'Device'} is offline`, true);
      return;
    }

    const turnOn = device.power !== 1;

    if (!turnOn && device.critical && device.known_key) {
      const primary = document.querySelector(`.power-tile[data-device="${device.known_key}"]`);
      if (primary) {
        closeHome();
        setTimeout(() => primary.click(), 120);
        return;
      }
    }

    card.classList.add('busy');
    try {
      let url = '/api/home/power';
      let body = {name: device.name, on: turnOn};
      if (device.known_key) {
        url = `/api/device/${encodeURIComponent(device.known_key)}/power`;
        body = {on: turnOn};
      }
      const res = await fetch(url, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      featureToast(`${device.name}: ${turnOn ? 'on' : 'off'}`);
      setTimeout(loadHomeDevices, 450);
    } catch (err) {
      featureToast(`${device.name}: ${err.message}`, true);
    } finally {
      card.classList.remove('busy');
    }
  });

  function renderUpdateStatus(state) {
    updateState = state;
    if (!updateControl) return;
    updateControl.classList.remove('update-available', 'update-current');
    updateStatusDot?.classList.remove('offline');

    if (state.error) {
      updateStatusText.textContent = 'check failed';
      updateStatusDot?.classList.add('offline');
      return;
    }
    if (state.available) {
      updateControl.classList.add('update-available');
      updateStatusText.textContent = `${state.commits_ahead || 1} update${state.commits_ahead === 1 ? '' : 's'}`;
    } else {
      updateControl.classList.add('update-current');
      updateStatusText.textContent = 'current';
    }
  }

  async function checkUpdate(silent = true) {
    if (!updateControl) return null;
    if (!silent) updateStatusText.textContent = 'checking…';
    try {
      const res = await fetch('/api/update/status', {cache:'no-store'});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      renderUpdateStatus(data);
      return data;
    } catch (err) {
      const failed = {error: err.message};
      renderUpdateStatus(failed);
      return failed;
    }
  }

  function renderUpdatePanel(state) {
    const localVersion = state.local_version ? `v${state.local_version}` : (state.local_head || '').slice(0,7);
    const remoteVersion = state.remote_version ? `v${state.remote_version}` : (state.remote_head || '').slice(0,7);
    updateVersion.textContent = state.available ? `${localVersion}  →  ${remoteVersion}` : `${localVersion} · current`;
    if (state.error) {
      updateSummary.textContent = state.error;
    } else if (state.available) {
      updateSummary.textContent = `${state.commits_ahead} new commit${state.commits_ahead === 1 ? '' : 's'} ready to install. The Pi will reboot automatically.`;
    } else {
      updateSummary.textContent = 'Aether Control is already up to date.';
    }

    const commits = state.commits || [];
    updateCommits.innerHTML = commits.length
      ? commits.map(c => `<div class="update-commit"><code>${escapeHtml(c.sha)}</code><span>${escapeHtml(c.subject)}</span></div>`).join('')
      : '<div class="update-commit"><span>No pending changelog entries.</span></div>';

    updateInstall.hidden = !state.available;
    updateInstall.disabled = false;
    updateInstall.textContent = 'install update';
    updateRollback.hidden = !state.rollback_available;
    updateRollback.disabled = false;
    updateRollback.textContent = 'rollback';
  }

  async function openUpdater() {
    if (!updatePanel) return;
    updatePanel.classList.add('show');
    updatePanel.setAttribute('aria-hidden', 'false');
    updateVersion.textContent = 'checking GitHub…';
    updateSummary.textContent = 'Looking for a newer Aether Control build.';
    updateCommits.innerHTML = '';
    updateInstall.hidden = true;
    updateRollback.hidden = true;
    const state = await checkUpdate(false);
    renderUpdatePanel(state || {error:'Could not check for updates'});
    resetIdleTimer();
  }

  function closeUpdater() {
    updatePanel?.classList.remove('show');
    updatePanel?.setAttribute('aria-hidden', 'true');
    resetIdleTimer();
  }

  updateControl?.addEventListener('click', openUpdater);
  updateClose?.addEventListener('click', closeUpdater);
  updatePanel?.addEventListener('click', e => { if (e.target === updatePanel) closeUpdater(); });

  updateInstall?.addEventListener('click', async () => {
    updateInstall.disabled = true;
    updateRollback.disabled = true;
    updateInstall.textContent = 'installing…';
    updateSummary.textContent = 'Pulling the new build. The Pi will reboot when the pull completes.';
    try {
      const res = await fetch('/api/update/install', {method:'POST', cache:'no-store'});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      updateInstall.textContent = 'rebooting…';
      updateSummary.textContent = data.message || 'Update installed. Rebooting Aether Control…';
      featureToast('Update installed · rebooting Pi');
    } catch (err) {
      updateInstall.disabled = false;
      updateRollback.disabled = false;
      updateInstall.textContent = 'install update';
      updateSummary.textContent = err.message;
      featureToast(`Update failed: ${err.message}`, true);
    }
  });

  updateRollback?.addEventListener('click', async () => {
    updateRollback.disabled = true;
    updateInstall.disabled = true;
    updateRollback.textContent = 'rolling back…';
    updateSummary.textContent = 'Restoring the previous Aether Control commit, then rebooting.';
    try {
      const res = await fetch('/api/update/rollback', {method:'POST', cache:'no-store'});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      updateSummary.textContent = data.message || 'Rollback complete. Rebooting…';
      featureToast('Rollback complete · rebooting Pi');
    } catch (err) {
      updateRollback.disabled = false;
      updateInstall.disabled = false;
      updateRollback.textContent = 'rollback';
      updateSummary.textContent = err.message;
      featureToast(`Rollback failed: ${err.message}`, true);
    }
  });

  function stateLabel(device) {
    if (!device || device.online === false) return {text:'offline', cls:'offline'};
    if (device.power === 1) return {text:'online', cls:'online'};
    if (device.power === 0) return {text:'off', cls:''};
    return {text:'unknown', cls:''};
  }

  function renderAmbient() {
    const now = new Date();
    if (ambientClock) ambientClock.textContent = now.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    if (ambientDate) ambientDate.textContent = now.toLocaleDateString([], {weekday:'long', month:'long', day:'numeric'});
    if (!ambientData) return;

    const weather = ambientData.weather || ambientData.rack || {};
    applyWeatherTheme(weather);
    ambientTemp.textContent = weather.online && weather.temperature != null ? `${Math.round(Number(weather.temperature))}°${weather.unit || 'F'}` : '--°';
    ambientCondition.textContent = weather.condition || (weather.online ? 'NWS weather' : 'weather offline');

    const rack = stateLabel(ambientData.devices?.['server-rack']);
    const aether = stateLabel(ambientData.devices?.aether);
    ambientRack.textContent = rack.text;
    ambientRack.className = rack.cls;
    ambientAether.textContent = aether.text;
    ambientAether.className = aether.cls;
    const net = Boolean(ambientData.network?.internet);
    ambientNetwork.textContent = net ? 'online' : 'offline';
    ambientNetwork.className = net ? 'online' : 'offline';
  }

  async function refreshAmbientData() {
    try {
      const res = await fetch('/api/dashboard', {cache:'no-store'});
      if (!res.ok) return;
      ambientData = await res.json();
      applyWeatherTheme(ambientData.weather || ambientData.rack || {});
      renderAmbient();
    } catch (_) {}
  }

  function showAmbient() {
    if (!ambientMode || homePanel?.classList.contains('show') || updatePanel?.classList.contains('show')) {
      resetIdleTimer();
      return;
    }
    refreshAmbientData();
    renderAmbient();
    ambientMode.classList.add('show');
    ambientMode.setAttribute('aria-hidden', 'false');
    if (!ambientPollTimer) ambientPollTimer = setInterval(refreshAmbientData, 30000);
  }

  function hideAmbient() {
    ambientMode?.classList.remove('show');
    ambientMode?.setAttribute('aria-hidden', 'true');
    if (ambientPollTimer) {
      clearInterval(ambientPollTimer);
      ambientPollTimer = null;
    }
    resetIdleTimer();
  }

  function resetIdleTimer() {
    clearTimeout(idleTimer);
    if (!ambientMode?.classList.contains('show')) idleTimer = setTimeout(showAmbient, IDLE_MS);
  }

  ambientMode?.addEventListener('pointerdown', event => {
    event.preventDefault();
    event.stopPropagation();
    hideAmbient();
  });
  ['pointerdown','keydown','wheel'].forEach(type => {
    document.addEventListener(type, () => {
      if (!ambientMode?.classList.contains('show')) resetIdleTimer();
    }, {passive:true});
  });

  setInterval(renderAmbient, 1000);
  setTimeout(() => checkUpdate(true), 9000);
  setInterval(() => checkUpdate(true), 15 * 60 * 1000);
  setTimeout(refreshAmbientData, 2500);
  setTimeout(refreshWeatherTheme, 1600);
  setInterval(refreshWeatherTheme, 5 * 60 * 1000);
  resetIdleTimer();
})();
