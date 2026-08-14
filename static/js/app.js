(() => {
  const pages = document.getElementById('pages');
  const viewport = document.getElementById('viewport');
  const tabs = [...document.querySelectorAll('.tab')];
  const dots = [...document.querySelectorAll('.page-dots i')];
  const powerTiles = [...document.querySelectorAll('.power-tile')];
  const modal = document.getElementById('confirmModal');
  const modalTitle = document.getElementById('modalTitle');
  const modalCopy = document.getElementById('modalCopy');
  const modalCancel = document.getElementById('modalCancel');
  const modalConfirm = document.getElementById('modalConfirm');
  const toast = document.getElementById('toast');
  const logList = document.getElementById('logList');
  const logFilter = document.getElementById('logFilter');

  const weatherAlert = document.getElementById('weatherAlert');
  const weatherDetail = document.getElementById('weatherDetail');
  let currentWeatherAlert = null;

  function applyAutomaticTheme() {
    const hour = new Date().getHours();
    const dark = hour >= 20 || hour < 7;
    document.body.classList.toggle('dark-mode', dark);
  }
  applyAutomaticTheme();
  setInterval(applyAutomaticTheme, 30000);

  function weatherAlertKey(alert) {
    return alert?.id || `${alert?.event || ''}|${alert?.headline || ''}|${alert?.expires || ''}`;
  }

  async function loadWeatherAlerts() {
    try {
      const res = await fetch('/api/weather/alerts', {cache:'no-store'});
      if (!res.ok) return;
      const data = await res.json();
      const alert = (data.alerts || [])[0];
      if (!alert) { weatherAlert.classList.remove('show'); return; }
      currentWeatherAlert = alert;
      const key = weatherAlertKey(alert);
      const dismissed = sessionStorage.getItem('dismissedWeatherAlert');
      if (dismissed === key) return;
      document.getElementById('weatherSeverity').textContent = `${alert.severity || 'Weather'} · ${alert.urgency || ''}`.toUpperCase();
      document.getElementById('weatherEvent').textContent = alert.event || 'Weather Alert';
      document.getElementById('weatherHeadline').textContent = alert.headline || 'Weather alert in effect';
      weatherAlert.classList.add('show');
      weatherAlert.setAttribute('aria-hidden', 'false');
    } catch (err) { console.error(err); }
  }

  document.getElementById('weatherDismiss').addEventListener('click', () => {
    if (currentWeatherAlert) sessionStorage.setItem('dismissedWeatherAlert', weatherAlertKey(currentWeatherAlert));
    weatherAlert.classList.remove('show');
    weatherAlert.setAttribute('aria-hidden', 'true');
  });
  document.getElementById('weatherDetails').addEventListener('click', () => {
    if (!currentWeatherAlert) return;
    document.getElementById('weatherDetailEvent').textContent = currentWeatherAlert.event || 'Weather Alert';
    document.getElementById('weatherDetailCopy').textContent = [currentWeatherAlert.headline, currentWeatherAlert.description, currentWeatherAlert.instruction].filter(Boolean).join('\n\n');
    weatherDetail.classList.add('show');
    weatherDetail.setAttribute('aria-hidden', 'false');
  });
  document.getElementById('weatherDetailClose').addEventListener('click', () => {
    weatherDetail.classList.remove('show');
    weatherDetail.setAttribute('aria-hidden', 'true');
  });

  let currentPage = 0;
  let pendingPower = null;
  let dashboardTimer = null;
  let systemTimer = null;
  let allLogs = [];
  let toastTimer = null;
  const localState = {};

  function setPage(index) {
    currentPage = Math.max(0, Math.min(2, index));
    pages.style.transform = `translate3d(-${currentPage * 100}vw,0,0)`;
    tabs.forEach((t, i) => t.classList.toggle('active', i === currentPage));
    dots.forEach((d, i) => d.classList.toggle('active', i === currentPage));
    if (currentPage === 1) loadLogs();
    if (currentPage === 2) loadSystem();
  }

  tabs.forEach(tab => tab.addEventListener('click', () => setPage(Number(tab.dataset.page))));

  let startX = 0;
  let startY = 0;
  let tracking = false;
  viewport.addEventListener('pointerdown', e => {
    if (modal.classList.contains('show')) return;
    startX = e.clientX;
    startY = e.clientY;
    tracking = true;
  });
  viewport.addEventListener('pointerup', e => {
    if (!tracking) return;
    tracking = false;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (Math.abs(dx) < 62 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
    if (dx < 0) setPage(currentPage + 1);
    else setPage(currentPage - 1);
  });
  viewport.addEventListener('pointercancel', () => tracking = false);

  function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
  }
  updateClock();
  setInterval(updateClock, 1000);

  function showToast(message, isError = false) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle('error', isError);
    toast.classList.add('show');
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2800);
  }

  function applyDeviceState(key, state) {
    const tile = document.querySelector(`[data-device="${key}"]`);
    if (!tile) return;
    const text = tile.querySelector('.state-text');
    tile.classList.remove('state-on', 'state-off', 'state-error');

    if (state?.error || state?.online === false) {
      tile.classList.add('state-error');
      text.textContent = 'UNREACHABLE';
    } else if (state?.power === 1) {
      tile.classList.add('state-on');
      text.textContent = tile.classList.contains('tile-feature') ? 'ONLINE' : 'ON';
    } else if (state?.power === 0) {
      tile.classList.add('state-off');
      text.textContent = 'POWERED OFF';
    } else {
      text.textContent = 'UNKNOWN';
    }
    localState[key] = state;
  }

  async function loadDashboard() {
    try {
      const res = await fetch('/api/dashboard', {cache:'no-store'});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      Object.entries(data.devices || {}).forEach(([key, state]) => applyDeviceState(key, state));

      const rackTemp = document.getElementById('rackTemp');
      const headerTemp = document.getElementById('headerTemp');
      const humidityText = document.getElementById('humidityText');
      const tempDot = document.getElementById('tempDot');
      if (data.rack?.online && data.rack?.temperature !== null && data.rack?.temperature !== undefined) {
        const value = Number(data.rack.temperature);
        const shown = Number.isFinite(value) ? Math.round(value) : data.rack.temperature;
        rackTemp.textContent = `${shown}°${data.rack.unit || 'F'}`;
        headerTemp.textContent = `${shown}°`;
        humidityText.textContent = data.rack.humidity == null ? 'sensor online' : `${Math.round(Number(data.rack.humidity))}% RH`;
        tempDot.classList.remove('offline');
      } else {
        rackTemp.textContent = '--°';
        headerTemp.textContent = '--°';
        humidityText.textContent = 'sensor offline';
        tempDot.classList.add('offline');
      }

      const internet = Boolean(data.network?.internet);
      document.getElementById('networkText').textContent = internet ? 'online' : 'offline';
      document.getElementById('networkDot').classList.toggle('offline', !internet);
      document.getElementById('headerNetwork').classList.toggle('online', internet);
    } catch (err) {
      document.getElementById('headerNetwork').classList.remove('online');
      console.error(err);
    }
  }

  function askPowerOff(tile) {
    const label = tile.dataset.label;
    pendingPower = {tile, key: tile.dataset.device, on: false};
    modalTitle.textContent = `Turn off ${label}?`;
    modalCopy.textContent = (tile.dataset.device === 'server-rack' || tile.dataset.device === 'aether')
      ? 'This cuts power at the smart plug immediately. Make sure the machine is safely shut down first.'
      : 'This will cut power at the Govee smart plug.';
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeModal() {
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');
    pendingPower = null;
  }

  async function sendPower(tile, on) {
    const key = tile.dataset.device;
    const label = tile.dataset.label;
    tile.classList.add('busy');
    const stateText = tile.querySelector('.state-text');
    stateText.textContent = on ? 'POWERING ON…' : 'POWERING OFF…';
    try {
      const res = await fetch(`/api/device/${encodeURIComponent(key)}/power`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({on})
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      applyDeviceState(key, data.device);
      showToast(`${label}: ${on ? 'power on' : 'power off'} sent`);
      setTimeout(loadDashboard, 900);
      if (currentPage === 1) setTimeout(loadLogs, 500);
    } catch (err) {
      showToast(`${label}: ${err.message}`, true);
      setTimeout(loadDashboard, 400);
    } finally {
      tile.classList.remove('busy');
    }
  }

  powerTiles.forEach(tile => {
    tile.addEventListener('click', () => {
      if (tile.classList.contains('busy')) return;
      const state = localState[tile.dataset.device];
      if (!state || state.online === false || state.error) {
        showToast(`${tile.dataset.label} is unreachable`, true);
        return;
      }
      if (state.power === 1) askPowerOff(tile);
      else sendPower(tile, true);
    });
  });

  modalCancel.addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
  modalConfirm.addEventListener('click', () => {
    if (!pendingPower) return;
    const {tile, on} = pendingPower;
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');
    pendingPower = null;
    sendPower(tile, on);
  });

  function logTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit', second:'2-digit'});
  }

  function renderLogs() {
    const filter = logFilter.value;
    const entries = allLogs.filter(e => filter === 'all' || e.level === filter);
    if (!entries.length) {
      logList.innerHTML = '<div class="log-empty">No matching events yet.</div>';
      return;
    }
    logList.innerHTML = entries.map(e => `
      <div class="log-entry ${escapeHtml(e.level)}">
        <div class="log-accent"></div>
        <div class="log-source">${escapeHtml(e.source)}</div>
        <div class="log-message">${escapeHtml(e.message)}</div>
        <div class="log-time">${escapeHtml(logTime(e.created_at))}</div>
      </div>`).join('');
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  }

  async function loadLogs() {
    try {
      const res = await fetch('/api/logs?limit=120', {cache:'no-store'});
      const data = await res.json();
      allLogs = data.events || [];
      renderLogs();
    } catch (err) {
      logList.innerHTML = '<div class="log-empty">Could not load logs.</div>';
    }
  }
  logFilter.addEventListener('change', renderLogs);
  document.getElementById('clearLogs').addEventListener('click', async () => {
    try {
      await fetch('/api/logs/clear', {method:'POST'});
      showToast('Logs cleared');
      loadLogs();
    } catch { showToast('Could not clear logs', true); }
  });

  function formatUptime(seconds) {
    seconds = Number(seconds || 0);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${mins}m`;
    return `${mins}m`;
  }

  async function loadSystem() {
    try {
      const res = await fetch('/api/system', {cache:'no-store'});
      const s = await res.json();
      document.getElementById('systemHost').textContent = s.hostname || 'controller';
      document.getElementById('cpuValue').textContent = `${s.cpu ?? '--'}%`;
      document.getElementById('memoryValue').textContent = `${s.memory ?? '--'}%`;
      document.getElementById('diskValue').textContent = `${s.disk ?? '--'}%`;
      document.getElementById('uptimeValue').textContent = formatUptime(s.uptime_seconds);
      document.getElementById('goveeValue').textContent = s.govee_configured ? 'READY' : 'NO KEY';
      document.getElementById('internetValue').textContent = s.internet ? 'ONLINE' : 'OFFLINE';
      document.getElementById('cpuMeter').style.width = `${Math.min(100, Number(s.cpu || 0))}%`;
      document.getElementById('memoryMeter').style.width = `${Math.min(100, Number(s.memory || 0))}%`;
      document.getElementById('diskMeter').style.width = `${Math.min(100, Number(s.disk || 0))}%`;
    } catch (err) { console.error(err); }
  }

  document.querySelectorAll('[data-action$="placeholder"]').forEach(tile => {
    tile.addEventListener('click', () => showToast('Coming in the next Aether Control build'));
  });
  document.querySelector('[data-action="network"]').addEventListener('click', () => setPage(2));
  document.querySelector('[data-action="temp"]').addEventListener('click', () => showToast('Rack sensor is live'));

  loadDashboard();
  dashboardTimer = setInterval(loadDashboard, 6000);
  systemTimer = setInterval(() => { if (currentPage === 2) loadSystem(); }, 5000);
  loadWeatherAlerts();
  setInterval(loadWeatherAlerts, 60000);
})();
