(() => {
  "use strict";

  const meta = (name) => document.querySelector(`meta[name="${name}"]`).content;
  const BASE = meta("base-path");
  const CSRF = meta("csrf-token");
  const PUBLIC_HOST = meta("public-host");
  const PANEL_VERSION = meta("panel-version");
  const state = { overview: null, users: [], limit: 10000, selectedUsers: new Set(), userSort: { key: "", direction: "asc" }, logs: [], logsMeta: null, editing: null, userRefreshTimer: null, historyPoints: [], tailscale: null, vkHashes: [], telegram: null };

  let panelVersionTimer = null;

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  function renderSidebarState() {
    const collapsed = document.body.classList.contains("sidebar-collapsed");
    $("#sidebar-toggle-icon").textContent = collapsed ? "›" : "‹";
    $("#sidebar-toggle").setAttribute("aria-label", collapsed ? "Развернуть боковое меню" : "Свернуть боковое меню");
    $("#sidebar-toggle").setAttribute("title", collapsed ? "Развернуть боковое меню" : "Свернуть боковое меню");
    $("#sidebar-toggle").setAttribute("aria-expanded", String(!collapsed));
  }

  function restoreSidebarState() {
    try { document.body.classList.toggle("sidebar-collapsed", localStorage.getItem("wdtt-sidebar-collapsed") === "1"); }
    catch (_) { /* Browser storage can be disabled. */ }
    renderSidebarState();
  }

  function renderTheme() {
    const light = document.body.classList.contains("light-theme");
    $("#theme-toggle-label").textContent = light ? "Тёмная тема" : "Светлая тема";
    $("#theme-toggle").setAttribute("aria-pressed", String(light));
  }

  function restoreTheme() {
    try { document.body.classList.toggle("light-theme", localStorage.getItem("wdtt-theme") === "light"); }
    catch (_) { /* Browser storage can be disabled. */ }
    renderTheme();
  }

  const ACCENTS = ["red", "orange", "yellow", "green", "blue", "purple"];

  function renderAccent() {
    const current = document.body.dataset.accent || "red";
    $$('#accent-picker [data-accent-option]').forEach((button) => {
      const active = button.dataset.accentOption === current;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-pressed", "true");
      else button.removeAttribute("aria-pressed");
    });
  }

  function applyAccent(name, persist = true) {
    const accent = ACCENTS.includes(name) ? name : "red";
    document.body.dataset.accent = accent;
    if (persist) {
      try { localStorage.setItem("wdtt-accent", accent); }
      catch (_) { /* Browser storage can be disabled. */ }
    }
    renderAccent();
    // Графики перекрашиваются сразу, из кэша точек (без запроса к API).
    if (Array.isArray(state.historyPoints)) drawCharts(state.historyPoints);
  }

  function restoreAccent() {
    let saved = "red";
    try { saved = localStorage.getItem("wdtt-accent") || saved; }
    catch (_) { /* Browser storage can be disabled. */ }
    // Неизвестная сохранённая схема переводится на стандартную красную
    // с перезаписью выбора, чтобы браузер не возвращался к несуществующей теме.
    applyAccent(saved, !ACCENTS.includes(saved));
  }

  function activateTab(tabName, persist = true) {
    const button = $$(".nav-item").find((item) => item.dataset.tab === tabName) || $(".nav-item");
    const name = button.dataset.tab;
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
    $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.id === `tab-${name}`));
    $("#page-title").textContent = button.textContent;
    if (persist) {
      try { localStorage.setItem("wdtt-active-tab", name); }
      catch (_) { /* Browser storage can be disabled. */ }
    }
    if (name === "logs") loadLogs();
    if (name === "wdtt") loadWdttSettings();
    if (name === "overview" && Array.isArray(state.historyPoints)) drawCharts(state.historyPoints);
    if (name === "system") { loadBackups(); loadBackupSchedule(); loadAutoclean(); loadAudit(); loadTelegramSettings(); loadTailscale(); loadPanelVersion(); }
  }

  function restoreActiveTab() {
    let tabName = "overview";
    try { tabName = localStorage.getItem("wdtt-active-tab") || tabName; }
    catch (_) { /* Browser storage can be disabled. */ }
    activateTab(tabName, false);
  }

  function setUserAutoRefresh(persist = true) {
    const select = $("#user-auto-refresh-interval");
    const seconds = Number(select.value) || 0;
    if (state.userRefreshTimer) clearInterval(state.userRefreshTimer);
    state.userRefreshTimer = null;
    $("#user-auto-refresh-status").textContent = seconds ? `Каждые ${seconds} сек.` : "Выключено";
    if (seconds) state.userRefreshTimer = setInterval(() => loadUsers().catch(() => {}), seconds * 1000);
    if (persist) {
      try { localStorage.setItem("wdtt-user-auto-refresh", String(seconds)); }
      catch (_) { /* Browser storage can be disabled. */ }
    }
  }

  function restoreUserAutoRefresh() {
    const select = $("#user-auto-refresh-interval");
    try {
      const saved = Number(localStorage.getItem("wdtt-user-auto-refresh"));
      if ([0, 5, 10, 30, 60].includes(saved)) select.value = String(saved);
    } catch (_) { /* Browser storage can be disabled. */ }
    setUserAutoRefresh(false);
  }
  const formatBytes = (bytes) => {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let size = value;
    let unit = -1;
    do { size /= 1024; unit += 1; } while (size >= 1024 && unit < units.length - 1);
    return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unit]}`;
  };
  const formatDate = (stamp) => stamp ? new Date(stamp * 1000).toLocaleString("ru-RU") : "Бессрочно";
  const formatActivityDate = (stamp) => stamp ? new Date(stamp * 1000).toLocaleString("ru-RU") : "ещё не было";
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));

  function dateInputValue(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 10);
  }

  function addCalendarMonths(date, months) {
    const result = new Date(date);
    const day = result.getDate();
    result.setDate(1);
    result.setMonth(result.getMonth() + months);
    result.setDate(Math.min(day, new Date(result.getFullYear(), result.getMonth() + 1, 0).getDate()));
    return result;
  }

  function setExpiryPreset(prefix, months, base = null) {
    const input = $(`#${prefix}-expires`);
    const start = base || (input.dataset.baseStamp ? new Date(Number(input.dataset.baseStamp) * 1000) : new Date());
    input.value = dateInputValue(addCalendarMonths(start, months));
    input.dataset.months = String(months);
    $$(`[data-expiry-preset^="${prefix}:"]`).forEach((button) => button.classList.toggle("active", button.dataset.expiryPreset === `${prefix}:${months}`));
    const traffic = $(`#${prefix}-traffic`) || $(`#${prefix}-primary`);
    if (traffic) traffic.value = String(35 * months);
  }

  function expirationPayload(prefix) {
    const input = $(`#${prefix}-expires`);
    if (input.dataset.months) return { months: Number(input.dataset.months) };
    const stamp = Math.floor(new Date(`${input.value}T23:59:59`).getTime() / 1000);
    return { expires_at: stamp };
  }

  function clearExpiryPreset(prefix) {
    const input = $(`#${prefix}-expires`);
    input.dataset.months = "";
    $$(`[data-expiry-preset^="${prefix}:"]`).forEach((button) => button.classList.remove("active"));
  }

  async function api(route, options = {}) {
    const init = { method: options.method || "GET", headers: { "Accept": "application/json" } };
    if (init.method === "POST") {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRF-Token"] = CSRF;
      init.body = JSON.stringify(options.body || {});
    }
    const response = await fetch(`${BASE}api/${route}`, init);
    const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    if (!response.ok || data.ok === false) throw new Error(data.error || "Ошибка запроса");
    return data.result ?? data;
  }

  function toast(message, error = false) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.toggle("error", error);
    node.hidden = false;
    clearTimeout(node.timer);
    node.timer = setTimeout(() => { node.hidden = true; }, 4200);
  }

  function setBusy(button, busy) {
    if (!button) return;
    button.disabled = busy;
    if (busy) button.dataset.label = button.textContent;
    button.textContent = busy ? "Выполнение..." : (button.dataset.label || button.textContent);
  }

  function renderVkHashLibrary() {
    const hashes = state.vkHashes;
    $("#vk-hashes-list").innerHTML = hashes.length ? hashes.map((hash) => `<div class="hash-library-row"><code>${escapeHtml(hash)}</code><button type="button" class="danger" data-delete-vk-hash="${escapeHtml(hash)}">Удалить</button></div>`).join("") : '<p class="muted">Библиотека пока пуста.</p>';
  }

  function renderVkHashPickers() {
    const options = state.vkHashes.map((hash) => `<option value="${escapeHtml(hash)}">${escapeHtml(hash)}</option>`).join("");
    ["#edit-saved-hash", "#bulk-saved-hash"].forEach((selector) => {
      const select = $(selector);
      select.innerHTML = `<option value="">Выберите хеш из библиотеки</option>${options}`;
    });
  }

  async function loadVkHashes() {
    const result = await api("vk-hashes");
    state.vkHashes = result.hashes || [];
    renderVkHashPickers();
    renderVkHashLibrary();
  }

  function appendSavedHash(selectId, inputId) {
    const select = $(selectId);
    const hash = select.value;
    select.value = "";
    if (!hash) return;
    const input = $(inputId);
    const hashes = input.value.trim() ? input.value.trim().split(/[,\s]+/).filter(Boolean) : [];
    if (hashes.includes(hash)) return;
    if (hashes.length >= 4) { toast("WDTT поддерживает не более четырех VK-хешей", true); return; }
    input.value = [...hashes, hash].join(",");
  }

  async function saveVkHashes(event) {
    event.preventDefault();
    if (event.submitter?.value === "cancel") { $("#vk-hashes-dialog").close(); return; }
    const input = $("#new-vk-hashes");
    if (!input.value.trim()) { toast("Укажите хотя бы один VK-хеш", true); return; }
    const button = $("#save-vk-hashes");
    setBusy(button, true);
    try {
      await api("vk-hashes", { method: "POST", body: { hashes: input.value } });
      input.value = "";
      await loadVkHashes();
      toast("Библиотека VK-хешей обновлена");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function deleteVkHash(hash) {
    try {
      await api("vk-hashes/delete", { method: "POST", body: { hash } });
      await loadVkHashes();
      toast("Хеш удалён из библиотеки");
    } catch (error) { toast(error.message, true); }
  }

  async function exportVkHashes() {
    try {
      const result = await api("vk-hashes/export");
      downloadText(result.name, result.content);
      toast(`Экспортировано VK-хешей: ${result.count || 0}`);
    } catch (error) { toast(error.message, true); }
  }

  async function importVkHashes(file) {
    if (!file) return;
    try {
      const result = await api("vk-hashes/import", { method: "POST", body: { name: file.name, content: await file.text() } });
      state.vkHashes = result.hashes || [];
      renderVkHashPickers();
      renderVkHashLibrary();
      toast(`Импортировано новых VK-хешей: ${result.imported || 0}`);
    } catch (error) { toast(error.message, true); }
    finally { $("#vk-hashes-upload").value = ""; }
  }

  function renderTelegramSettings() {
    const settings = state.telegram || {};
    const enabled = Boolean(settings.enabled);
    $("#telegram-enabled").checked = enabled;
    $("#telegram-admin-id").value = settings.admin_id || "";
    $("#telegram-bot-token").value = "";
    $("#telegram-bot-token").placeholder = settings.bot_token_set ? `Сохранён: ${settings.bot_token_hint}` : "123456:ABC...";
    $("#telegram-status").className = `badge ${enabled ? "ok" : "warn"}`;
    $("#telegram-status").textContent = enabled ? "включён" : "выключен";
    $("#telegram-info").textContent = enabled
      ? `Бот привязан к Admin ID ${settings.admin_id}. Пустой token при сохранении оставит текущий ключ.`
      : "Бот выключен. Для включения укажите Bot Token и Admin ID.";
  }

  async function loadTelegramSettings() {
    try {
      state.telegram = await api("telegram");
      renderTelegramSettings();
    } catch (error) {
      $("#telegram-info").textContent = error.message;
      $("#telegram-status").className = "badge bad";
      $("#telegram-status").textContent = "ошибка";
    }
  }

  async function saveTelegramSettings() {
    const button = $("#save-telegram");
    setBusy(button, true);
    try {
      state.telegram = await api("telegram/save", { method: "POST", body: {
        enabled: $("#telegram-enabled").checked,
        admin_id: $("#telegram-admin-id").value.trim(),
        bot_token: $("#telegram-bot-token").value.trim(),
      }});
      renderTelegramSettings();
      toast("Настройки Telegram сохранены");
      await loadOverview();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function testTelegramSettings() {
    const button = $("#test-telegram");
    setBusy(button, true);
    try {
      await api("telegram/test", { method: "POST", body: { message: `WDTT Control Panel ${PANEL_VERSION}: тест Telegram` } });
      toast("Тестовое сообщение отправлено в Telegram");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function renderTailscale(result) {
    state.tailscale = result;
    const online = Boolean(result.online);
    $("#tailscale-status").className = `badge ${online ? "ok" : (result.installed ? "warn" : "bad")}`;
    $("#tailscale-status").textContent = online ? "в сети" : (result.daemon ? "отключён" : (result.installed ? "демон не отвечает" : "не установлен"));
    if (result.hostname) $("#tailscale-hostname").value = result.hostname;
    $("#tailscale-accept-dns").checked = result.accept_dns !== false;
    if (result.authkey_set) $("#tailscale-auth-key").placeholder = `Сохранён: ${result.authkey_hint}`;
    const where = [result.dns_name, (result.tailscale_ips || []).join(", ")].filter(Boolean).join(" · ");
    $("#tailscale-info").textContent = !result.installed
      ? "Установите компонент, затем вставьте reusable Auth Key из admin console Tailscale и нажмите «Подключить»."
      : (online ? `В сети: ${where || result.hostname}. Пустой ключ при подключении использует сохранённый.` : "Отключён. Пустой ключ при подключении использует сохранённый.");
    $("#install-tailscale").textContent = result.installed ? "Tailscale установлен" : "Установить Tailscale";
  }

  async function loadTailscale() {
    try { renderTailscale(await api("tailscale")); }
    catch (error) {
      $("#tailscale-info").textContent = error.message;
      $("#tailscale-status").className = "badge bad";
      $("#tailscale-status").textContent = "ошибка";
    }
  }

  async function installTailscale() {
    const button = $("#install-tailscale"); setBusy(button, true);
    try { await api("tailscale/install", { method: "POST" }); toast("Установка Tailscale запущена"); setTimeout(loadTailscale, 10000); }
    catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function connectTailscale() {
    const button = $("#connect-tailscale"); setBusy(button, true);
    try {
      renderTailscale(await api("tailscale/connect", { method: "POST", body: {
        auth_key: $("#tailscale-auth-key").value.trim(),
        hostname: $("#tailscale-hostname").value.trim(),
        accept_dns: $("#tailscale-accept-dns").checked,
      }}));
      $("#tailscale-auth-key").value = "";
      toast("Tailscale подключён");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function disconnectTailscale() {
    const button = $("#disconnect-tailscale"); setBusy(button, true);
    try { renderTailscale(await api("tailscale/disconnect", { method: "POST" })); toast("Tailscale отключён (вход сохранён)"); }
    catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function logoutTailscale() {
    if (!confirm("Выйти из tailnet? Для повторного входа понадобится Auth Key.")) return;
    const button = $("#logout-tailscale"); setBusy(button, true);
    try { renderTailscale(await api("tailscale/logout", { method: "POST" })); toast("Выход из tailnet выполнен"); }
    catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function checkTailscale() {
    const button = $("#check-tailscale"); setBusy(button, true);
    try {
      const result = await api("tailscale/check", { method: "POST" });
      const latency = (result.latency_ms === null || result.latency_ms === undefined) ? "" : ` за ${result.latency_ms} мс`;
      const derp = result.nearest_derp ? `, DERP ${result.nearest_derp}` : "";
      $("#tailscale-info").textContent = `Tailscale в сети${result.udp === false ? " (UDP закрыт — только реле)" : ""}${derp}${latency}.`;
      toast("Tailscale отвечает");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function healthRow(label, ok, value) {
    return `<div class="health-item"><span><i class="status-dot ${ok ? "ok" : "bad"}"></i>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
  }

  async function loadOverview() {
    const overview = await api("overview");
    state.overview = overview;
    const service = overview.service || {};
    const stats = overview.stats || {};
    const active = Boolean(service.active);
    $("#service-label").textContent = active ? "Сервис работает" : "Сервис остановлен";
    $("#service-detail").textContent = active ? `NAT: ${stats.nat || "определяется"} · uptime ${stats.uptime || "0м"}` : "Активные туннели недоступны";
    $("#service-dot").className = `status-dot large ${active ? "ok" : "bad"}`;
    $("#sidebar-dot").className = `status-dot ${active ? "ok" : "bad"}`;
    $("#sidebar-status").textContent = active ? "WDTT активен" : "WDTT остановлен";
    $("#active-conns").textContent = stats.active ?? 0;
    $("#total-conns").textContent = `${stats.total ?? 0} всего`;
    $("#user-count").textContent = overview.users ?? 0;
    $("#device-count").textContent = `${overview.devices ?? 0} устройств · онлайн: ${overview.online_devices ?? 0}`;
    const up = Number(stats.up_gb || 0), down = Number(stats.down_gb || 0);
    $("#traffic-total").textContent = `${(up + down).toFixed(2)} GB`;
    $("#traffic-split").textContent = `↑${up.toFixed(2)} / ↓${down.toFixed(2)}`;
    const system = overview.system || {}, memory = system.memory || {}, disk = overview.disk || {};
    $("#cpu-load").textContent = `${Number(system.cpu_percent || 0).toFixed(1)}%`;
    $("#system-load").textContent = `load ${Number((system.load_average || [0])[0]).toFixed(2)}`;
    $("#memory-load").textContent = `${Number(memory.percent || 0).toFixed(1)}%`;
    $("#memory-detail").textContent = `${formatBytes(memory.used)} / ${formatBytes(memory.total)}`;
    $("#disk-load").textContent = `${Number(disk.percent || 0).toFixed(1)}%`;
    $("#disk-detail").textContent = `${formatBytes(disk.used)} / ${formatBytes(disk.total)}`;
    $("#health-list").innerHTML = [
      healthRow(service.docker ? "runtime (docker)" : "systemd unit", service.exists, service.exists ? "найден" : "не найден"),
      healthRow("wdtt-server", service.binary, service.binary ? "установлен" : "отсутствует"),
      healthRow("IPv4 forwarding", String(service.ip_forward) === "1", String(service.ip_forward) === "1" ? "включен" : "выключен"),
      ...wdttListenerRows(overview.wdtt || {}),
    ].join("");
    renderCertificate(overview.certificate || {});
    await loadHistory();
  }

  function wdttListenerRows(wdtt) {
    const settings = (wdtt && wdtt.settings) || {};
    const directPort = Number(settings.direct_port || 0), rawPort = Number(settings.raw_port || 0);
    const rows = [];
    if (directPort > 0) rows.push(healthRow(`Direct UDP :${directPort}`, Boolean(wdtt.direct_listening), wdtt.direct_listening ? "слушается" : "не слушается"));
    if (rawPort > 0) rows.push(healthRow(`RAW UDP :${rawPort}`, Boolean(wdtt.raw_listening), wdtt.raw_listening ? "слушается" : "не слушается"));
    return rows;
  }

  function renderCertificate(cert) {
    const rows = [];
    rows.push(`<div class="detail-row"><span>Режим</span><strong>${escapeHtml(cert.mode || "неизвестно")}</strong></div>`);
    if (cert.host) rows.push(`<div class="detail-row"><span>Домен</span><strong>${escapeHtml(cert.host)}</strong></div>`);
    rows.push(`<div class="detail-row"><span>Файл</span><strong>${cert.exists ? "найден" : "не найден"}</strong></div>`);
    rows.push(`<div class="detail-row"><span>HTTPS локально</span><strong>${cert.local_tls_ok ? "работает" : "не отвечает"}</strong></div>`);
    rows.push(`<div class="detail-row"><span>Порт</span><strong>${cert.listening ? "слушается" : "не слушается"}</strong></div>`);
    if (cert.expires_at) rows.push(`<div class="detail-row"><span>Истекает</span><strong>${escapeHtml(formatDate(cert.expires_at))}</strong></div>`);
    if (cert.days_left !== undefined && cert.days_left !== null) rows.push(`<div class="detail-row"><span>Осталось</span><strong>${escapeHtml(cert.days_left)} дней</strong></div>`);
    if (cert.error) rows.push(`<div class="detail-row"><span>Ошибка</span><strong>${escapeHtml(cert.error)}</strong></div>`);
    if (cert.mode === "self-signed") rows.push(`<p class="muted">Self-signed сертификат шифрует соединение, но браузер покажет предупреждение, пока сертификат не добавлен в доверенные.</p>`);
    $("#certificate-info").innerHTML = rows.join("") || `<p class="muted">Данные сертификата недоступны.</p>`;
  }

  async function loadHistory() {
    const history = await api("history");
    state.historyPoints = history.points || [];
    drawCharts(state.historyPoints);
  }

  // Рисуем графики только когда вкладка «Обзор» видима и канвас имеет размер:
  // иначе скрытый канвас рисуется в минимальный 600px и CSS растягивает его
  // (размытие при возврате на вкладку). Перерисовка — при активации вкладки,
  // смене акцента/темы и по поллу.
  function drawCharts(points) {
    const tab = $("#tab-dashboard");
    if (!tab || !tab.classList.contains("active")) return false;
    const conns = $("#conns-chart");
    if (!conns || conns.getBoundingClientRect().width < 40) return false;
    drawConnsChart(points);
    drawSpeedChart(points);
    return true;
  }

  function setupCanvas(id, height) {
    const canvas = $(id);
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(600, rect.width * ratio);
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, canvas.width / ratio, height);
    return [ctx, canvas.width / ratio, height];
  }

  function chartColors() {
    const css = getComputedStyle(document.body);
    const pick = (name, fallback) => (css.getPropertyValue(name) || "").trim() || fallback;
    return { accent: pick("--accent", "#7fb5a6"), line: pick("--line", "#2e2c25"), muted: pick("--muted", "#a39d8f"), faint: pick("--faint", "#6b665b") };
  }

  function withAlpha(color, alpha) {
    if (color[0] === "#") {
      const hex = color.slice(1);
      const full = hex.length === 3 ? hex.split("").map((c) => c + c).join("") : hex.slice(0, 6);
      const n = parseInt(full, 16);
      return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
    }
    const m = color.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(",").map((s) => parseFloat(s)); return `rgba(${p[0]}, ${p[1]}, ${p[2]}, ${alpha})`; }
    return color;
  }

  // Максимум шкалы «красивый»: четыре деления ряда 1/1.5/2/2.5/3/4/5/6/8/10 × 10^k.
  function niceMax(value) {
    const target = Math.max(1e-9, value) / 4;
    const pow = Math.pow(10, Math.floor(Math.log10(target)));
    const steps = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
    const step = steps.find((s) => s * pow >= target - 1e-9) || 10;
    return step * pow * 4;
  }

  // Оси: горизонтали с подписями min→max слева, вертикали с временем снизу.
  function drawChartFrame(ctx, width, height, points, max, colors, unit) {
    const pad = { left: 46, right: 12, top: 14, bottom: 24 };
    const x0 = pad.left, x1 = width - pad.right;
    const y0 = pad.top, y1 = height - pad.bottom;
    const X = (i) => x0 + (points.length > 1 ? (i / (points.length - 1)) * (x1 - x0) : (x1 - x0) / 2);
    const Y = (v) => y1 - (v / max) * (y1 - y0);
    const step = max / 4;
    const label = step < 0.5 ? (v) => v.toFixed(2) : step < 1 ? (v) => v.toFixed(1) : (v) => String(Math.round(v));
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (let f = 0; f <= 4; f++) {
      const v = (max * f) / 4;
      const y = Y(v);
      ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      ctx.fillStyle = (f === 0 || f === 4) ? colors.muted : colors.faint;
      ctx.fillText(label(v), x0 - 7, y);
    }
    if (points.length > 1) {
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      [0, 0.25, 0.5, 0.75, 1].forEach((f) => {
        const i = Math.round(f * (points.length - 1));
        const x = X(i);
        ctx.strokeStyle = colors.line;
        ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
        const stamp = Number(points[i][0] || 0);
        if (stamp) {
          ctx.textAlign = f === 0 ? "left" : f === 1 ? "right" : "center";
          ctx.fillStyle = colors.faint;
          ctx.fillText(new Date(stamp * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }), f === 0 ? x0 : f === 1 ? x1 : x, y1 + 6);
        }
      });
    }
    if (unit) {
      ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.fillStyle = colors.faint;
      ctx.fillText(unit, 2, 1);
    }
    return { X, Y, x0, x1, y0, y1 };
  }

  function traceLine(ctx, scale, values) {
    ctx.beginPath();
    values.forEach((v, i) => { i ? ctx.lineTo(scale.X(i), scale.Y(v)) : ctx.moveTo(scale.X(i), scale.Y(v)); });
  }

  function strokeSolid(ctx, scale, values, color, width) {
    traceLine(ctx, scale, values);
    ctx.strokeStyle = color; ctx.lineWidth = width || 2; ctx.lineCap = "round"; ctx.stroke();
  }

  function strokeDashed(ctx, scale, values, color) {
    traceLine(ctx, scale, values);
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]); ctx.lineCap = "round"; ctx.stroke(); ctx.setLineDash([]);
  }

  function fillArea(ctx, scale, values, colors) {
    const gradient = ctx.createLinearGradient(0, scale.y0, 0, scale.y1);
    gradient.addColorStop(0, withAlpha(colors.accent, .3)); gradient.addColorStop(1, withAlpha(colors.accent, 0));
    traceLine(ctx, scale, values);
    ctx.lineTo(scale.x1, scale.y1); ctx.lineTo(scale.x0, scale.y1); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
  }

  function chartPlaceholder(ctx, width, height, points, max, colors) {
    const scale = drawChartFrame(ctx, width, height, points, max, colors, "");
    ctx.fillStyle = colors.faint; ctx.font = "12px sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillText("История появится после нескольких обновлений", scale.x0 + 8, scale.y0 + 6);
  }

  function drawConnsChart(points) {
    // Формат точек: [captured_at, active, total, up_gb, down_gb, users, devices].
    const [ctx, width, height] = setupCanvas("#conns-chart", 220);
    const colors = chartColors();
    if (points.length < 2) { chartPlaceholder(ctx, width, height, [], 4, colors); return; }
    const active = points.map((item) => Number(item[1] || 0));
    const max = niceMax(Math.max(4, ...active));
    const scale = drawChartFrame(ctx, width, height, points, max, colors, "шт.");
    fillArea(ctx, scale, active, colors);
    strokeSolid(ctx, scale, active, colors.accent, 2);
  }

  // Окно истории: показываем с первого «живого» мгновения минус зазор (10 мин),
  // максимум 24 часа назад от последней точки, минимум 2 точки для отрисовки.
  function historyWindow(points, leadSeconds) {
    const lead = leadSeconds || 600;
    const firstRise = (() => {
      for (let i = 1; i < points.length; i++) {
        const dt = Number(points[i][0] || 0) - Number(points[i - 1][0] || 0);
        const dg = (Number(points[i][4] || 0) - Number(points[i - 1][4] || 0))
          + (Number(points[i][3] || 0) - Number(points[i - 1][3] || 0));
        if (dt > 0 && dg > 0) return i;
      }
      return -1;
    })();
    if (firstRise < 1) return points;
    const last = Number(points[points.length - 1][0] || 0);
    const start = Math.max(
      Number(points[firstRise][0] || 0) - lead,
      last - 86400,
      Number(points[0][0] || 0),
    );
    const trimmed = points.filter((p) => Number(p[0] || 0) >= start);
    return trimmed.length >= 2 ? trimmed : points.slice(-2);
  }

  function drawSpeedChart(points) {
    // Скорость в Мбит/с — производная счётчиков ГБ по времени; сброс счётчика даёт 0.
    const [ctx, width, height] = setupCanvas("#speed-chart", 220);
    const colors = chartColors();
    points = historyWindow(points);
    if (points.length < 2) { chartPlaceholder(ctx, width, height, [], 4, colors); return; }
    const rate = (idx) => points.map((item, i) => {
      if (!i) return 0;
      const dt = Number(points[i][0] || 0) - Number(points[i - 1][0] || 0);
      const dg = Number(item[idx] || 0) - Number(points[i - 1][idx] || 0);
      return dt > 0 && dg > 0 ? (dg * 8000) / dt : 0;
    });
    const down = rate(4), up = rate(3);
    const max = niceMax(Math.max(1, ...down, ...up));
    const scale = drawChartFrame(ctx, width, height, points, max, colors, "Мбит/с");
    fillArea(ctx, scale, down, colors);
    strokeSolid(ctx, scale, down, colors.accent, 2);
    strokeDashed(ctx, scale, up, colors.muted);
  }

  async function loadUsers() {
    const result = await api("users");
    state.users = [...(result.admins || []), ...(result.users || [])];
    const available = new Set((result.users || []).map((user) => user.password));
    state.selectedUsers.forEach((password) => { if (!available.has(password)) state.selectedUsers.delete(password); });
    state.limit = result.limit || 10000;
    $("#user-limit").textContent = `${(result.users || []).length} / ${state.limit}`;
    renderUsers();
  }

  function userStatus(user) {
    if (user.connected) return ["ok", "подключен"];
    if (user.expired) return ["bad", "истек"];
    if (user.is_deactivated) return ["warn", "выключен"];
    if (user.device_id) return ["warn", "не в сети"];
    return ["ok", "свободен"];
  }

  function lastUserActivity(user) {
    return Math.max(
      Number(user.last_handshake || 0),
      Number(user.last_upload_at || 0),
      Number(user.last_download_at || 0),
    );
  }

  function lastUserActivityKind(user) {
    const stamp = lastUserActivity(user);
    if (!stamp) return "ещё не было";
    if (stamp === Number(user.last_download_at || 0)) return "загрузка";
    if (stamp === Number(user.last_upload_at || 0)) return "отправка";
    return "подключение";
  }

  function userSortValue(user, key) {
    if (key === "label") return user.label || "";
    if (key === "access") return user.password || "";
    if (key === "status") {
      if (user.connected) return 0;
      if (!user.expired && !user.is_deactivated && !user.device_id) return 1;
      if (!user.expired && !user.is_deactivated) return 2;
      if (user.is_deactivated) return 3;
      return 4;
    }
    if (key === "expires") return user.expires_at || Number.MAX_SAFE_INTEGER;
    if (key === "device") return `${user.device?.device_id || user.device_id || ""} ${user.device?.ip || ""}`;
    if (key === "devices") return (user.device_ids || user.devices || []).length;
    if (key === "traffic") return Number(user.down_bytes || 0) + Number(user.up_bytes || 0);
    if (key === "speed") return Number(user.max_down_mbps || 0) + Number(user.max_up_mbps || 0);
    if (key === "activity") return lastUserActivity(user);
    return "";
  }

  function sortedUsers(users) {
    const { key, direction } = state.userSort;
    if (!key) return users;
    const factor = direction === "desc" ? -1 : 1;
    return [...users].sort((left, right) => {
      const first = userSortValue(left, key);
      const second = userSortValue(right, key);
      const comparison = typeof first === "number" && typeof second === "number"
        ? first - second : String(first).localeCompare(String(second), "ru", { numeric: true, sensitivity: "base" });
      return comparison * factor || String(left.password).localeCompare(String(right.password), "ru", { numeric: true });
    });
  }

  function renderUserSortControls() {
    $$("[data-user-sort]").forEach((button) => {
      const active = button.dataset.userSort === state.userSort.key;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
      const indicator = button.querySelector(".sort-indicator");
      if (indicator) indicator.textContent = active ? (state.userSort.direction === "asc" ? "↑" : "↓") : "↕";
    });
  }

  function renderUsers() {
    const query = $("#user-search").value.toLowerCase();
    const users = sortedUsers(state.users.filter((user) => JSON.stringify(user).toLowerCase().includes(query)));
    $("#users-body").innerHTML = users.map((user) => {
      const [statusClass, status] = userStatus(user);
      const quotaManaged = user.role !== "admin" && user.traffic_managed;
      const quotaUsed = Number(user.traffic_quota_used_bytes || 0);
      const quotaLimit = Number(user.traffic_limit_bytes || 0);
      const quotaPercent = quotaLimit ? Math.min(100, Math.round(quotaUsed * 100 / quotaLimit)) : 0;
      const warning = quotaManaged && !user.traffic_unlimited && quotaPercent >= 80
        ? `<small class="quota-warning ${quotaPercent >= 100 ? "quota-danger" : ""}">${quotaPercent >= 100 ? "Лимит исчерпан" : `Использовано ${quotaPercent}%`}</small>` : "";
      const quotaText = quotaManaged ? (user.traffic_unlimited ? "Без лимита" : `${formatBytes(user.traffic_remaining_bytes)} из ${formatBytes(quotaLimit)} осталось`) : "";
      const traffic = user.traffic_supported === false ? "Появится после включения" : `${formatBytes(user.down_bytes)} ↓ / ${formatBytes(user.up_bytes)} ↑${quotaText ? `<br><small>${escapeHtml(quotaText)}</small>${warning}` : ""}`;
      const deviceItems = (user.devices && user.devices.length) ? user.devices : [];
      const deviceUsed = (user.device_ids && user.device_ids.length) ? user.device_ids.length : deviceItems.length;
      const deviceLimit = Number(user.max_devices || 0);
      const deviceTotal = deviceLimit >= 10000 ? "∞" : String(Math.max(1, deviceLimit));
      const devicesCell = user.role === "admin"
        ? "<span class=\"muted\">—</span>"
        : `<strong>${deviceUsed}</strong> / ${deviceTotal}`;
      const device = deviceItems.length
        ? deviceItems.map((item) => `${escapeHtml(item.device_id)}<br><small>${escapeHtml(item.ip || "")}${item.connected ? " · онлайн" : ""}</small>`).join("<br>")
        : (user.device ? `${escapeHtml(user.device.device_id || user.device_id)}<br><small>${escapeHtml(user.device.ip || "")}</small>` : "Не привязан");
      const title = user.label || user.password;
      const selectable = user.role !== "admin";
      const lastActivity = lastUserActivity(user);
      const speedDown = Number(user.max_down_mbps || 0), speedUp = Number(user.max_up_mbps || 0);
      const speed = user.role === "admin"
        ? "<span class=\"muted\">—</span>"
        : ((!speedDown && !speedUp) ? "Без ограничения" : `${speedDown ? `↓ ${speedDown}` : "↓ ∞"} / ${speedUp ? `↑ ${speedUp}` : "↑ ∞"}<br><small>Мбит/с</small>`);
      return `<tr>
        <td>${selectable ? `<input type="checkbox" data-select-user="${escapeHtml(user.password)}" aria-label="Выбрать ${escapeHtml(title)}" ${state.selectedUsers.has(user.password) ? "checked" : ""}>` : ""}</td>
        <td>${user.label ? `<strong>${escapeHtml(user.label)}</strong>` : "<span class=\"muted\">—</span>"}</td>
        <td><strong class="mono">${escapeHtml(user.password)}</strong><br><small>${escapeHtml(user.vk_hash)}</small></td>
        <td><span class="badge ${statusClass}">${status}</span></td>
        <td>${escapeHtml(formatDate(user.expires_at))}</td>
        <td class="mono">${device}</td><td class="mono" title="Занято / доступно">${devicesCell}</td><td>${traffic}</td><td>${speed}</td>
        <td><strong>${escapeHtml(formatActivityDate(lastActivity))}</strong><br><small>${escapeHtml(lastUserActivityKind(user))}</small></td>
        <td><button class="user-actions-trigger" data-actions-toggle="${escapeHtml(user.password)}">Действия</button></td></tr>`;
    }).join("") || `<tr><td colspan="11" class="muted">Пользователи не найдены.</td></tr>`;
    closeUserActions();
    renderUserSortControls();
    renderSelectedUsersControls();
  }

  function ensureUserActionsPopover() {
    let popover = $("#user-actions-popover");
    if (!popover) {
      popover = document.createElement("div");
      popover.id = "user-actions-popover";
      popover.className = "user-actions-popover";
      popover.hidden = true;
      document.body.appendChild(popover);
    }
    return popover;
  }

  function closeUserActions() {
    const popover = $("#user-actions-popover");
    if (!popover) return;
    popover.hidden = true;
    popover.dataset.password = "";
  }

  function openUserActions(button, user) {
    if (!user) return;
    const popover = ensureUserActionsPopover();
    popover.dataset.password = user.password;
    popover.innerHTML = `
      <button data-activity="${escapeHtml(user.password)}">Активность</button>
      ${user.role === "admin" ? "" : `<button data-copy="${escapeHtml(user.password)}" title="Скопировать wdtt:// ссылку">Ссылка</button>`}
      ${user.role === "admin" ? "" : `
      <button data-personal="${escapeHtml(user.password)}">Личная страница</button>
      <button data-renew="${escapeHtml(user.password)}">Продлить</button>
      ${user.traffic_unlimited ? "" : `<button data-extra="${escapeHtml(user.password)}">Добавить трафик</button>`}
      <button data-edit="${escapeHtml(user.password)}">Изменить</button>
      ${user.device_id ? `<button data-unbind="${escapeHtml(user.password)}">Отвязать</button>` : ""}
      <button data-reset="${escapeHtml(user.password)}">Сброс трафика</button>
      <button data-delete="${escapeHtml(user.password)}">Удалить</button>`}
    `;
    popover.hidden = false;
    popover.style.visibility = "hidden";
    const rect = button.getBoundingClientRect();
    const menu = popover.getBoundingClientRect();
    let left = rect.left - menu.width - 8;
    if (left < 8) left = rect.right + 8;
    left = Math.min(Math.max(8, left), window.innerWidth - menu.width - 8);
    const top = Math.min(Math.max(8, rect.top), window.innerHeight - menu.height - 8);
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    popover.style.visibility = "";
  }

  async function handleUserActionButton(button) {
    const find = (password) => state.users.find((item) => item.password === password);
    if (button.dataset.activity) openUserActivity(find(button.dataset.activity));
    if (button.dataset.edit) openUserDialog(find(button.dataset.edit));
    if (button.dataset.renew) openQuotaDialog(find(button.dataset.renew), "renew");
    if (button.dataset.extra) openQuotaDialog(find(button.dataset.extra), "extra");
    if (button.dataset.unbind) userAction("users/unbind", button.dataset.unbind, "Отвязать устройство? Следующее подключение создаст новую привязку.");
    if (button.dataset.reset) userAction("users/reset-traffic", button.dataset.reset, "Сбросить счетчики трафика пользователя?");
    if (button.dataset.delete) userAction("users/delete", button.dataset.delete, "Удалить пользователя и его устройство без возможности отмены?");
    if (button.dataset.copy) {
      const user = find(button.dataset.copy);
      await navigator.clipboard.writeText(quickLink(user));
      toast("Ссылка wdtt:// скопирована");
    }
    if (button.dataset.personal) {
      const user = find(button.dataset.personal);
      await navigator.clipboard.writeText(user.personal_url);
      toast("Ссылка на личную страницу скопирована");
    }
  }

  function renderSelectedUsersControls() {
    const selected = state.selectedUsers.size;
    const action = $("#bulk-user-action").value;
    $("#selected-users").textContent = `Выбрано: ${selected}`;
    $("#bulk-user-days-control").hidden = action !== "set_expiration";
    $("#apply-bulk-user-action").disabled = !selected || !action;
    const query = $("#user-search").value.toLowerCase();
    const visible = state.users.filter((user) => user.role !== "admin" && JSON.stringify(user).toLowerCase().includes(query));
    const all = $("#select-all-users");
    all.checked = visible.length > 0 && visible.every((user) => state.selectedUsers.has(user.password));
    all.indeterminate = visible.some((user) => state.selectedUsers.has(user.password)) && !all.checked;
  }

  function openUserDialog(user = null) {
    state.editing = user;
    $("#dialog-title").textContent = user ? "Изменить пользователя" : "Новый пользователь";
    $("#current-password").value = user?.password || "";
    $("#edit-password").value = user?.password || "";
    $("#edit-password").placeholder = user ? "Пароль доступа" : "Пусто = создать автоматически";
    $("#edit-hashes").value = user?.vk_hash || "";
    $("#edit-label").value = user?.role === "admin" ? "" : (user?.label || "");
    $("#edit-ports").value = user?.ports || "56000,56001,9000";
    $("#edit-max-devices").value = String(user?.max_devices ?? 10000);
    $("#edit-max-down").value = String(user?.max_down_mbps ?? 0);
    $("#edit-max-up").value = String(user?.max_up_mbps ?? 0);
    $("#edit-unlimited").checked = Boolean(user && !user.expires_at);
    $("#edit-unlimited").disabled = Boolean(user);
    $("#edit-disabled").checked = Boolean(user?.is_deactivated);
    const accessPlan = $("#edit-access-plan");
    accessPlan.hidden = Boolean(user);
    accessPlan.querySelectorAll("input,button").forEach((item) => { item.disabled = Boolean(user); });
    $("#edit-traffic-unlimited").checked = false;
    $("#edit-traffic").disabled = false;
    setExpiryPreset("edit", 1, new Date());
    $("#user-dialog").showModal();
  }

  function openQuotaDialog(user, mode) {
    if (!user) return;
    $("#quota-password").value = user.password;
    $("#quota-mode").value = mode;
    $("#quota-title").textContent = mode === "renew" ? `Продлить: ${user.label || user.password}` : `Добавить трафик: ${user.label || user.password}`;
    $("#quota-renew-fields").hidden = mode !== "renew";
    $("#quota-extra-fields").hidden = mode !== "extra";
    $("#quota-comment").value = "";
    $("#quota-extra").value = "10";
    const baseStamp = user.expires_at > Date.now() / 1000 ? user.expires_at : Math.floor(Date.now() / 1000);
    $("#quota-expires").dataset.baseStamp = String(baseStamp);
    setExpiryPreset("quota", 1);
    $("#quota-dialog").showModal();
  }

  async function saveQuota(event) {
    event.preventDefault();
    if (event.submitter?.value === "cancel") { $("#quota-dialog").close(); return; }
    const button = $("#save-quota");
    const mode = $("#quota-mode").value;
    const payload = {
      password: $("#quota-password").value,
      operation_id: (crypto.randomUUID ? crypto.randomUUID() : `quota-${Date.now()}-${Math.random().toString(16).slice(2)}`),
      comment: $("#quota-comment").value,
    };
    if (mode === "renew") Object.assign(payload, expirationPayload("quota"), { traffic_primary_gib: Number($("#quota-primary").value) });
    else payload.gib = Number($("#quota-extra").value);
    setBusy(button, true);
    try {
      await api(mode === "renew" ? "users/renew" : "users/add-traffic", { method: "POST", body: payload });
      $("#quota-dialog").close();
      toast(mode === "renew" ? "Срок и основной трафик обновлены" : "Дополнительный трафик добавлен");
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function openUserActivity(user) {
    if (!user) return;
    const title = user.label || user.password;
    const rows = [
      ["Метка", user.label || "—"],
      ["Пароль", user.password],
      ["Статус", userStatus(user)[1]],
    ];
    const devices = (user.devices && user.devices.length) ? user.devices : [];
    if (devices.length <= 1) {
      const device = user.device || {};
      rows.push(["Устройство", device.device_id || user.device_id || "не привязано"]);
      rows.push(["IP устройства", device.ip || "—"]);
    } else {
      rows.push(["Устройств", String(devices.length)]);
      devices.forEach((item) => {
        rows.push([`${item.device_id}${item.connected ? " · онлайн" : ""}`, `${item.ip || "—"}`]);
      });
    }
    rows.push(["Последнее подключение", formatActivityDate(user.last_handshake)]);
    rows.push(["Последняя отправка", formatActivityDate(user.last_upload_at)]);
    rows.push(["Последняя загрузка", formatActivityDate(user.last_download_at)]);
    rows.push(["Отправлено всего", formatBytes(user.up_bytes)]);
    rows.push(["Загружено всего", formatBytes(user.down_bytes)]);
    rows.push(["Скорость вниз", Number(user.max_down_mbps || 0) ? `${user.max_down_mbps} Мбит/с` : "без ограничения"]);
    rows.push(["Скорость вверх", Number(user.max_up_mbps || 0) ? `${user.max_up_mbps} Мбит/с` : "без ограничения"]);
    $("#user-activity-title").textContent = `Активность: ${title}`;
    $("#user-activity-details").innerHTML = rows.map(([label, value]) => `<div class="detail-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    $("#user-activity-dialog").showModal();
  }

  async function saveUser(event) {
    event.preventDefault();
    if (event.submitter?.value === "cancel") { $("#user-dialog").close(); return; }
    const button = $("#save-user");
    setBusy(button, true);
    const payload = {
      password: $("#edit-password").value,
      label: $("#edit-label").value,
      vk_hash: $("#edit-hashes").value,
      ports: $("#edit-ports").value,
      max_devices: Number($("#edit-max-devices").value),
      max_down_mbps: Number($("#edit-max-down").value),
      max_up_mbps: Number($("#edit-max-up").value),
      is_deactivated: $("#edit-disabled").checked,
    };
    if (state.editing) payload.current_password = state.editing.password;
    else Object.assign(payload, $("#edit-unlimited").checked ? { unlimited: true } : expirationPayload("edit"), {
      traffic_primary_gib: Number($("#edit-traffic").value),
      traffic_unlimited: $("#edit-traffic-unlimited").checked,
    });
    try {
      await api(state.editing ? "users/update" : "users/create", { method: "POST", body: payload });
      $("#user-dialog").close();
      toast(state.editing ? "Пользователь обновлен" : "Пользователь создан");
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function openAutoUserDialog() {
    const remaining = Math.max(0, state.limit - state.users.filter((user) => user.role !== "admin").length);
    if (!remaining) { toast(`Достигнут лимит ${state.limit} пользователей`, true); return; }
    $("#auto-label").value = "";
    $("#auto-user-dialog").showModal();
    $("#auto-label").focus();
  }

  async function saveAutoUser(event) {
    event.preventDefault();
    if (event.submitter?.value === "cancel") { $("#auto-user-dialog").close(); return; }
    const label = $("#auto-label").value.trim();
    if (!label) { toast("Укажите метку пользователя", true); return; }
    const button = $("#save-auto-user");
    setBusy(button, true);
    try {
      const user = await api("users/create-auto", { method: "POST", body: { label } });
      $("#bulk-result-links").value = quickLink(user);
      $("#auto-user-dialog").close();
      $("#bulk-result-dialog").showModal();
      toast("Пользователь создан автоматически");
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function openBulkUserDialog() {
    const remaining = Math.max(0, state.limit - state.users.filter((user) => user.role !== "admin").length);
    if (!remaining) { toast(`Достигнут лимит ${state.limit} пользователей`, true); return; }
    $("#bulk-count").max = remaining;
    $("#bulk-count").value = Math.min(2, remaining);
    $("#bulk-traffic-unlimited").checked = false;
    $("#bulk-traffic").disabled = false;
    $("#bulk-max-devices").value = 10000;
    $("#bulk-max-down").value = 0;
    $("#bulk-max-up").value = 0;
    $("#bulk-expires").disabled = false;
    setExpiryPreset("bulk", 1, new Date());
    $("#bulk-user-dialog").showModal();
  }

  async function saveBulkUsers(event) {
    event.preventDefault();
    if (event.submitter?.value === "cancel") { $("#bulk-user-dialog").close(); return; }
    const button = $("#save-bulk-users");
    setBusy(button, true);
    const payload = {
      count: Number($("#bulk-count").value),
      vk_hash: $("#bulk-hashes").value,
      hash_mode: $("#bulk-hash-mode").value,
      label_prefix: $("#bulk-label-prefix").value,
      ports: $("#bulk-ports").value,
      max_devices: Number($("#bulk-max-devices").value),
      max_down_mbps: Number($("#bulk-max-down").value),
      max_up_mbps: Number($("#bulk-max-up").value),
      ...($("#bulk-unlimited").checked ? { unlimited: true } : expirationPayload("bulk")),
      traffic_primary_gib: Number($("#bulk-traffic").value),
      traffic_unlimited: $("#bulk-traffic-unlimited").checked,
      is_deactivated: $("#bulk-disabled").checked,
    };
    try {
      const result = await api("users/create-bulk", { method: "POST", body: payload });
      const users = result.users || [];
      $("#bulk-result-links").value = users.map(quickLink).join("\n");
      $("#bulk-user-dialog").close();
      $("#bulk-result-dialog").showModal();
      toast(`Создано пользователей: ${users.length}`);
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function copyBulkLinks() {
    try {
      await navigator.clipboard.writeText($("#bulk-result-links").value);
      toast("Все ссылки wdtt:// скопированы");
    } catch (error) { toast(`Не удалось скопировать: ${error.message}`, true); }
  }

  async function userAction(route, password, confirmText) {
    if (confirmText && !confirm(confirmText)) return;
    try {
      await api(route, { method: "POST", body: { password } });
      toast("Операция выполнена");
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (error) { toast(error.message, true); }
  }

  async function applyBulkUserAction() {
    const action = $("#bulk-user-action").value;
    const passwords = [...state.selectedUsers];
    if (!action || !passwords.length) return;
    const descriptions = {
      activate: "Активировать",
      deactivate: "Деактивировать",
      set_expiration: `Установить срок ${Number($("#bulk-user-days").value)} дней для`,
      reset_traffic: "Сбросить трафик у",
      unbind: "Отвязать устройства у",
      delete: "Удалить",
    };
    const destructive = action === "delete" ? " Это действие нельзя отменить." : "";
    if (!confirm(`${descriptions[action]} пользователей: ${passwords.length}?${destructive}`)) return;
    const button = $("#apply-bulk-user-action");
    setBusy(button, true);
    try {
      const payload = { action, passwords };
      if (action === "set_expiration") payload.days = Number($("#bulk-user-days").value);
      const result = await api("users/bulk-action", { method: "POST", body: payload });
      state.selectedUsers.clear();
      $("#bulk-user-action").value = "";
      toast(`Выполнено: ${result.count || passwords.length}`);
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function quickLink(user) {
    const ports = (user.ports || "56000,56001,9000").split(",");
    return `wdtt://${PUBLIC_HOST}:${ports[0]}:${ports[1]}:${ports[2]}:${user.password}:${user.vk_hash}`;
  }

  async function loadLogs() {
    try {
      const source = encodeURIComponent($("#log-source").value);
      const limit = encodeURIComponent($("#log-limit").value);
      const result = await api(`logs?source=${source}&limit=${limit}`);
      state.logs = result.lines || [];
      state.logsMeta = result;
      renderLogs();
    } catch (error) { $("#logs-output").textContent = error.message; }
  }

  function renderLogs() {
    const filter = $("#log-filter").value;
    const lines = filter ? state.logs.filter((line) => line.includes(filter)) : state.logs;
    $("#logs-output").textContent = lines.join("\n") || "Нет строк для выбранного фильтра.";
    const meta = state.logsMeta || {};
    const states = (meta.units || []).map((item) => `${item.unit}: ${item.active ? "работает" : "не активна"}`).join(" · ");
    $("#logs-summary").textContent = `${meta.title || "Журнал"}: загружено ${state.logs.length} строк${filter ? `, после фильтра ${lines.length}` : ""}${states ? ` · ${states}` : ""}`;
  }

  function downloadLogs() {
    const meta = state.logsMeta || {};
    if (!state.logs.length) { toast("Сначала загрузите журнал", true); return; }
    const header = [`# ${meta.title || "WDTT diagnostics"}`, `# Exported: ${new Date().toISOString()}`, ""].join("\n");
    downloadText(`wdtt-diagnostics-${meta.source || "logs"}.log`, `${header}${state.logs.join("\n")}\n`, "text/plain;charset=utf-8");
  }

  function cleanupPayload() {
    const targets = [];
    if ($("#cleanup-service-logs").checked) targets.push("service_logs");
    if ($("#cleanup-journal").checked) targets.push("journal");
    if ($("#cleanup-package-cache").checked) targets.push("package_cache");
    if ($("#cleanup-failed-units").checked) targets.push("failed_units");
    return { keep_days: Number($("#cleanup-keep-days").value) || 14, targets };
  }

  function renderCleanupResult(result) {
    const items = result.items || [];
    const rows = [
      ["Режим", result.applied ? "Очистка выполнена" : "Предпросмотр"],
      [result.applied ? "Освобождено" : "Можно очистить", formatBytes(result.estimated_freed_bytes || 0)],
      ["Journal хранится", `${result.keep_days || 14} дней`],
    ];
    items.forEach((item) => {
      const freedLabel = result.applied ? `освобождено ${formatBytes(item.freed_bytes || 0)}` : `можно очистить ${formatBytes(item.freed_bytes || 0)}`;
      const remaining = result.applied && item.remaining_bytes !== undefined ? `осталось ${formatBytes(item.remaining_bytes || 0)}` : "";
      if (item.target === "service_logs") {
        const files = item.files || [];
        const existing = files.filter((file) => file.exists).length;
        const skipped = files.filter((file) => file.skipped || file.error).length;
        const details = [freedLabel, `${existing} файлов`];
        if (remaining) details.push(remaining);
        if (skipped) details.push(`пропущено: ${skipped}`);
        if (item.error) details.push(item.error);
        rows.push(["Журналы служб", details.join(" · ")]);
      } else if (item.target === "package_cache") {
        rows.push(["Кэш пакетов", item.error || [freedLabel, remaining, item.command].filter(Boolean).join(" · ")]);
      } else if (item.target === "journal") {
        const journalDetails = [freedLabel, remaining, item.detail].filter(Boolean).join(" · ");
        rows.push(["Systemd journal", item.error || (item.available === false ? item.detail || "недоступен" : journalDetails || "готов")]);
      } else if (item.target === "failed_units") {
        rows.push(["Failed-units", item.error || (item.available === false ? "недоступно" : "готово")]);
      }
    });
    $("#cleanup-info").innerHTML = rows.map(([label, value]) => `<div class="detail-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  }

  async function cleanupSystem(apply, button) {
    const payload = cleanupPayload();
    if (!payload.targets.length) { toast("Выберите хотя бы один раздел очистки", true); return; }
    if (apply && !confirm("Очистить выбранные журналы и системный кэш? База WDTT, резервные копии и настройки не будут затронуты.")) return;
    setBusy(button, true);
    try {
      const result = await api(apply ? "cleanup/apply" : "cleanup/preview", { method: "POST", body: payload });
      renderCleanupResult(result);
      toast(apply ? "Очистка завершена" : "Оценка очистки готова");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function serviceAction(action, button) {
    if (action === "stop" && !confirm("Остановить WDTT и разорвать активные туннели?")) return;
    setBusy(button, true);
    try {
      await api("service", { method: "POST", body: { service_action: action } });
      toast(`Сервис: ${action}`);
      setTimeout(loadOverview, 900);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function loadBackups() {
    try {
      const result = await api("backups");
      $("#backups-list").innerHTML = (result.backups || []).map((item) => {
        const type = item.type === "full" ? "Полная панель" : "Пользователи WDTT";
        return `<div class="backup-row"><span><strong>${escapeHtml(item.name)}</strong><br><small>${escapeHtml(type)} · ${escapeHtml(formatDate(item.created_at))} · ${formatBytes(item.size)}</small></span><div class="row-actions"><button data-download-backup="${escapeHtml(item.name)}">Скачать</button><button data-restore="${escapeHtml(item.name)}" data-backup-type="${escapeHtml(item.type || "users")}">Восстановить</button><button data-delete-backup="${escapeHtml(item.name)}" class="danger">Удалить</button></div></div>`;
      }).join("") || `<p class="muted">Резервные копии появятся после первого сохранения.</p>`;
    } catch (error) { toast(error.message, true); }
  }

  async function loadBackupSchedule() {
    try {
      const result = await api("backups/schedule");
      const schedule = result.settings || {};
      $("#backup-schedule-frequency").value = schedule.frequency || "disabled";
      $("#backup-schedule-time").value = schedule.time || "03:30";
      $("#backup-schedule-type").value = schedule.type || "full";
      $("#backup-schedule-keep").value = schedule.keep || 14;
      $("#backup-schedule-status").textContent = schedule.frequency === "disabled"
        ? "Автоматические копии выключены."
        : `${schedule.frequency === "daily" ? "Каждый день" : "По воскресеньям"} в ${schedule.time}; хранится последних: ${schedule.keep}.`;
    } catch (error) { $("#backup-schedule-status").textContent = error.message; }
  }

  async function saveBackupSchedule() {
    const button = $("#save-backup-schedule");
    setBusy(button, true);
    try {
      await api("backups/schedule", { method: "POST", body: {
        frequency: $("#backup-schedule-frequency").value,
        time: $("#backup-schedule-time").value,
        type: $("#backup-schedule-type").value,
        keep: Number($("#backup-schedule-keep").value),
      }});
      toast("Расписание резервных копий сохранено");
      await loadBackupSchedule();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function loadAutoclean() {
    try {
      const result = await api("autoclean");
      const settings = result.settings || {};
      $("#autoclean-enabled").checked = settings.enabled !== false;
      $("#autoclean-disk-percent").value = settings.disk_percent || 90;
      $("#autoclean-keep-panel").value = settings.keep_panel || 20;
      $("#autoclean-keep-users").value = settings.keep_users || 20;
      $("#autoclean-keep-days").value = settings.keep_days || 14;
      const disk = result.disk_percent === undefined ? "" : ` Диск занят: ${result.disk_percent}%.`;
      $("#autoclean-status").textContent = (result.active ? "Таймер авто-очистки активен." : "Таймер авто-очистки выключен.") + disk;
    } catch (error) { $("#autoclean-status").textContent = error.message; }
  }

  async function saveAutoclean() {
    const button = $("#save-autoclean");
    setBusy(button, true);
    try {
      await api("autoclean/settings", { method: "POST", body: {
        enabled: $("#autoclean-enabled").checked,
        disk_percent: Number($("#autoclean-disk-percent").value),
        keep_panel: Number($("#autoclean-keep-panel").value),
        keep_users: Number($("#autoclean-keep-users").value),
        keep_days: Number($("#autoclean-keep-days").value),
      }});
      toast("Настройки авто-очистки сохранены");
      await loadAutoclean();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function runAutoclean() {
    const button = $("#run-autoclean");
    if (!confirm("Запустить проверку диска и очистку прямо сейчас?")) return;
    setBusy(button, true);
    try {
      const result = await api("autoclean/run", { method: "POST", body: {} });
      if (result.ran) {
        toast(`Очистка выполнена: освобождено ${formatBytes(result.freed_bytes || 0)}, удалено копий: ${result.backups_removed || 0}`);
      } else if (result.reason === "below_threshold") {
        toast(`Диск занят ${result.disk_percent}% (порог ${result.threshold}%) — очистка не требуется`);
      } else {
        toast("Авто-очистка выключена");
      }
      await loadAutoclean();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function createBackup(type) {
    const button = type === "full" ? $("#create-full-backup") : $("#create-users-backup");
    setBusy(button, true);
    try {
      const result = await api("backups/create", { method: "POST", body: { type } });
      toast(`${type === "full" ? "Полная копия" : "Копия пользователей"} создана: ${result.name}`);
      await loadBackups();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function renderPanelVersion(result) {
    const info = $("#panel-version-info");
    const updateButton = $("#update-panel");
    const checkButton = $("#check-panel-update");
    const current = result.current || PANEL_VERSION;
    const latest = result.latest || "";
    const rows = [`<div class="detail-row"><span>Установлена</span><strong>v${escapeHtml(current)}</strong></div>`];
    if (result.docker) {
      rows.push('<div class="detail-row"><span>Обновление</span><strong>Docker: панель — git pull на хосте + docker compose restart wdtt; ядро — docker compose up -d --build</strong></div>');
      info.innerHTML = rows.join("");
      updateButton.hidden = true;
      if (checkButton) checkButton.hidden = true;
      $("#panel-version-pill").textContent = `v${current}`;
      return;
    }
    if (latest) rows.push(`<div class="detail-row"><span>В репозитории</span><strong>v${escapeHtml(latest)}</strong></div>`);
    if (result.state === "running" || result.state === "checking") {
      rows.push('<div class="detail-row"><span>Проверка</span><strong>выполняется…</strong></div>');
    } else if (result.state === "error") {
      rows.push(`<div class="detail-row"><span>Проверка</span><strong>${escapeHtml(result.message || "ошибка")}</strong></div>`);
    } else if (result.checked_at) {
      rows.push(`<div class="detail-row"><span>Проверено</span><strong>${escapeHtml(formatDate(result.checked_at))}</strong></div>`);
    }
    info.innerHTML = rows.join("");
    updateButton.hidden = !(result.update_available || result.state === "error");
    updateButton.textContent = result.update_available && latest ? `Обновить до v${latest}` : "Обновить панель";
    $("#panel-version-pill").textContent = result.update_available && latest ? `v${current} → v${latest}` : `v${current}`;
  }

  function schedulePanelVersionPoll(attempt = 0, baseline = 0) {
    if (panelVersionTimer) clearTimeout(panelVersionTimer);
    panelVersionTimer = setTimeout(async () => {
      panelVersionTimer = null;
      try {
        const result = await api("panel/version");
        // Фоновая проверка стартует с задержкой (systemd-run --on-active=2s) и
        // сначала статус-файл содержит предыдущий результат. Ждём именно свежий
        // ответ (checked_at вырос), иначе текст версии не обновится без перезагрузки.
        const fresh = !baseline || Number(result.checked_at || 0) > baseline;
        if (((result.state === "running" || result.state === "checking") || !fresh) && attempt < 12) {
          schedulePanelVersionPoll(attempt + 1, baseline);
          return;
        }
        renderPanelVersion(result);
      } catch (_) {
        if (attempt < 12) schedulePanelVersionPoll(attempt + 1, baseline);
      }
    }, 2500);
  }

  async function loadPanelVersion(autoCheck = true) {
    let result;
    try { result = await api("panel/version"); }
    catch (error) {
      $("#panel-version-info").innerHTML = `<p class="muted">Не удалось получить версию панели: ${escapeHtml(error.message)}</p>`;
      return;
    }
    renderPanelVersion(result);
    if (result.docker) return; // в Docker кнопочного самообновления нет — не дёргаем check
    if (!autoCheck) return;
    const busy = result.state === "running" || result.state === "checking";
    const stale = !result.checked_at || (Math.floor(Date.now() / 1000) - Number(result.checked_at)) > 600;
    if (busy) schedulePanelVersionPoll(0);
    else if (stale) checkPanelUpdate(false);
  }

  async function checkPanelUpdate(manual = true) {
    let snapshot = null;
    try { snapshot = await api("panel/version"); }
    catch (error) { if (manual) toast(error.message, true); }
    try { await api("panel/check", { method: "POST" }); }
    catch (error) { if (manual) toast(error.message, true); return; }
    if (manual) toast("Проверяю обновления…");
    if (snapshot) renderPanelVersion({ ...snapshot, state: "checking", message: "" });
    schedulePanelVersionPoll(0, Number((snapshot && snapshot.checked_at) || 0));
  }

  async function updatePanel() {
    const button = $("#update-panel");
    if (!confirm("Обновить панель? Репозиторий будет принудительно синхронизирован с origin (git reset --hard), затем переустановка с сохранением конфига и перезапуск панели, ядра WDTT и демонов. Панель временно отключится.")) return;
    setBusy(button, true);
    try {
      await api("panel/update", { method: "POST" });
      toast("Обновление запущено. Панель перезапустится автоматически.");
    } catch (error) {
      toast(error.message, true);
      setBusy(button, false);
      return;
    }
    let attempts = 0;
    const timer = setInterval(async () => {
      attempts += 1;
      try {
        const result = await api("panel/version");
        if (result.state === "error") {
          clearInterval(timer);
          toast(result.message || "Обновление завершилось ошибкой", true);
          setBusy(button, false);
          loadPanelVersion(false);
          return;
        }
        if (result.state === "done") {
          clearInterval(timer);
          toast("Панель обновлена. Перезагружаю страницу…");
          setTimeout(() => location.reload(), 1500);
          return;
        }
      } catch (_) { /* панель перезапускается */ }
      if (attempts >= 30) {
        clearInterval(timer);
        toast("Обновление выполняется. Перезагрузите страницу позже.");
        setBusy(button, false);
      }
    }, 4000);
  }

  async function loadAudit() {
    const result = await api("audit");
    $("#audit-body").innerHTML = (result.items || []).map((item) => `<tr><td>${escapeHtml(formatDate(item[0]))}</td><td>${escapeHtml(item[1])}</td><td class="mono">${escapeHtml(item[2])}</td><td>${escapeHtml(item[3])}</td><td><span class="badge ${item[4] === "ok" ? "ok" : "bad"}">${escapeHtml(item[4])}</span></td></tr>`).join("");
  }

  async function restoreBackup(name, type) {
    const scope = type === "full" ? "пользователи и настройки панели" : "пользователи WDTT";
    if (!confirm(`Восстановить ${name}? Будут заменены: ${scope}. Перед этим будет создана полная резервная копия текущего состояния.`)) return;
    try {
      const result = await api("backups/restore", { method: "POST", body: { name } });
      toast(result.warnings?.length ? `Копия восстановлена, но есть предупреждения: ${result.warnings[0]}` : "Резервная копия восстановлена");
      await Promise.all([loadUsers(), loadOverview(), loadBackups()]);
    } catch (error) { toast(error.message, true); }
  }

  function downloadText(name, content, type = "application/json") {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const link = document.createElement("a");
    link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function downloadBackup(name) {
    try {
      const result = await api(`backups/export?name=${encodeURIComponent(name)}`);
      downloadText(result.name, result.content);
    } catch (error) { toast(error.message, true); }
  }

  async function uploadBackup(file) {
    if (!file) return;
    try {
      const result = await api("backups/import", { method: "POST", body: { name: file.name, content: await file.text() } });
      toast(`Backup загружен: ${result.name}`);
      await loadBackups();
    } catch (error) { toast(error.message, true); }
  }

  async function renewCertificate() {
    const button = $("#renew-certificate"); setBusy(button, true);
    try { await api("certificate/renew", { method: "POST" }); toast("Проверка сертификата запущена"); setTimeout(loadOverview, 7000); }
    catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function downloadCertificate() {
    try {
      const result = await api("certificate/export");
      downloadText(result.name, result.content, "application/x-pem-file");
    } catch (error) { toast(error.message, true); }
  }

  const WDTT_FIELDS = [
    ["listen_host", "wdtt-listen-host"],
    ["dtls_port", "wdtt-dtls-port"],
    ["wg_port", "wdtt-wg-port"],
    ["dns", "wdtt-dns"],
    ["direct_port", "wdtt-direct-port"],
    ["raw_port", "wdtt-raw-port"],
    ["max_users", "wdtt-max-users"],
    ["admin_listen", "wdtt-admin-listen"],
    ["handshake_timeout_s", "wdtt-handshake-timeout"],
    ["first_packet_timeout_s", "wdtt-first-packet-timeout"],
    ["wg_keepalive_s", "wdtt-wg-keepalive"],
    ["stats_interval_s", "wdtt-stats-interval"],
    ["wg_mtu", "wdtt-mtu"],
    ["max_dtls_per_device", "wdtt-max-dtls"],
    ["online_window_s", "wdtt-online-window"],
  ];

  async function loadWdttSettings() {
    try {
      const overview = await api("overview");
      const wdtt = overview.wdtt || {};
      const settings = wdtt.settings || {};
      WDTT_FIELDS.forEach(([key, id]) => { $(`#${id}`).value = settings[key] ?? ""; });
      const current = Boolean(wdtt.extension_current);
      $("#wdtt-extension-status").textContent = current
        ? `расширение ${wdtt.extension_marker || ""} активно`
        : "расширение ядра устарело: расширенные параметры применятся после обновления панели";
      $("#wdtt-tunables-note").hidden = current;
      renderWdttModes(settings, wdtt);
    } catch (error) { toast(error.message, true); }
  }

  function renderWdttModes(settings, wdtt) {
    const info = $("#wdtt-modes-info");
    if (!info) return;
    const directPort = Number(settings.direct_port || 0), rawPort = Number(settings.raw_port || 0);
    const parts = [];
    if (directPort > 0) parts.push(
      `Direct UDP :${directPort} — ${wdtt.direct_listening ? "слушается" : "не слушается"}. ` +
      `Клиент: go_client -peer ${PUBLIC_HOST}:${directPort} -password ПАРОЛЬ -vk ХЕШ -notls`);
    if (rawPort > 0) parts.push(
      `RAW UDP :${rawPort} — ${wdtt.raw_listening ? "слушается" : "не слушается"}. ` +
      `Клиент: только Android rawtun.`);
    if (!parts.length) info.textContent = "Direct и RAW выключены (порты 0): работает только DTLS → WireGuard. Ссылки wdtt:// и телефонное приложение других режимов не знают — direct/raw для go_client.";
    else info.innerHTML = parts.map(escapeHtml).join("<br>");
  }

  async function saveWdttSettings() {
    const button = $("#save-wdtt-settings"); setBusy(button, true);
    const payload = {};
    WDTT_FIELDS.forEach(([key, id]) => { payload[key] = $(`#${id}`).value; });
    try {
      const result = await api("wdtt/save", { method: "POST", body: payload });
      if (result.extension_pending) toast("Сохранено, но ядро без расширения v10: расширенные параметры пропущены до обновления");
      else toast(result.restarted ? "Настройки WDTT применены, ядро перезапущено" : "Настройки WDTT сохранены");
      await Promise.all([loadWdttSettings(), loadOverview()]);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function changeDomain() {
    const host = $("#domain-host").value.trim();
    const email = $("#domain-email").value.trim();
    if (!host) { toast("Укажите домен или IP", true); return; }
    if (!confirm(`Привязать панель к ${host}? Сертификат будет перевыпущен, панель перезапустится.`)) return;
    const button = $("#change-domain"); setBusy(button, true);
    try {
      const scheduled = await api("certificate/change-domain", { method: "POST", body: { host, email } });
      if (scheduled && scheduled.docker) toast("Смена домена запущена; после применения обновите PANEL_HOST в docker/.env на хосте");
      else toast("Смена домена запущена, жду применения…");
      for (let attempt = 0; attempt < 36; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        try {
          const overview = await api("overview");
          if ((overview.certificate || {}).host === host.toLowerCase()) {
            const pill = document.querySelector(".host-pill:last-of-type");
            if (pill) pill.textContent = host.toLowerCase();
            const meta = document.querySelector('meta[name="public-host"]');
            if (meta) meta.setAttribute("content", host.toLowerCase());
            toast(`Панель привязана к ${host}`);
            await loadOverview();
            return;
          }
        } catch (_) { /* панель перезапускается */ }
      }
      toast("Домен пока не применился — проверьте журнал установщика", true);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function deleteBackup(name) {
    if (!confirm(`Удалить ${name} без возможности восстановления?`)) return;
    try {
      await api("backups/delete", { method: "POST", body: { name } });
      toast("Резервная копия удалена");
      await loadBackups();
    } catch (error) { toast(error.message, true); }
  }

  function bindEvents() {
    $$(".nav-item").forEach((button) => button.addEventListener("click", () => activateTab(button.dataset.tab)));
    $("#sidebar-toggle").addEventListener("click", () => {
      document.body.classList.toggle("sidebar-collapsed");
      try { localStorage.setItem("wdtt-sidebar-collapsed", document.body.classList.contains("sidebar-collapsed") ? "1" : "0"); }
      catch (_) { /* Browser storage can be disabled. */ }
      renderSidebarState();
    });
    $("#theme-toggle").addEventListener("click", () => {
      document.body.classList.toggle("light-theme");
      try { localStorage.setItem("wdtt-theme", document.body.classList.contains("light-theme") ? "light" : "dark"); }
      catch (_) { /* Browser storage can be disabled. */ }
      renderTheme();
      if (Array.isArray(state.historyPoints)) drawCharts(state.historyPoints);
    });
    const accentPicker = $("#accent-picker");
    if (accentPicker) accentPicker.addEventListener("click", (event) => {
      const button = event.target.closest("[data-accent-option]");
      if (button) applyAccent(button.dataset.accentOption);
    });
    $("#manage-vk-hashes").addEventListener("click", async () => {
      try { await loadVkHashes(); $("#vk-hashes-dialog").showModal(); }
      catch (error) { toast(error.message, true); }
    });
    $("#new-user").addEventListener("click", () => openUserDialog());
    $("#auto-user").addEventListener("click", openAutoUserDialog);
    $("#bulk-users").addEventListener("click", openBulkUserDialog);
    $("#user-form").addEventListener("submit", saveUser);
    $("#auto-user-form").addEventListener("submit", saveAutoUser);
    $("#bulk-user-form").addEventListener("submit", saveBulkUsers);
    $("#quota-form").addEventListener("submit", saveQuota);
    $$('[data-expiry-preset]').forEach((button) => button.addEventListener("click", () => {
      const [prefix, rawMonths] = button.dataset.expiryPreset.split(":");
      setExpiryPreset(prefix, Number(rawMonths));
    }));
    ["edit", "bulk", "quota"].forEach((prefix) => $(`#${prefix}-expires`).addEventListener("change", () => clearExpiryPreset(prefix)));
    $("#edit-traffic-unlimited").addEventListener("change", (event) => { $("#edit-traffic").disabled = event.target.checked; });
    $("#bulk-traffic-unlimited").addEventListener("change", (event) => { $("#bulk-traffic").disabled = event.target.checked; });
    $("#edit-unlimited").addEventListener("change", (event) => { $("#edit-expires").disabled = event.target.checked || Boolean(state.editing); });
    $("#bulk-unlimited").addEventListener("change", (event) => { $("#bulk-expires").disabled = event.target.checked; });
    $("#vk-hashes-form").addEventListener("submit", saveVkHashes);
    $("#export-vk-hashes").addEventListener("click", exportVkHashes);
    $("#import-vk-hashes").addEventListener("click", () => $("#vk-hashes-upload").click());
    $("#vk-hashes-upload").addEventListener("change", (event) => importVkHashes(event.target.files[0]));
    $("#edit-saved-hash").addEventListener("change", () => appendSavedHash("#edit-saved-hash", "#edit-hashes"));
    $("#bulk-saved-hash").addEventListener("change", () => appendSavedHash("#bulk-saved-hash", "#bulk-hashes"));
    $("#vk-hashes-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-delete-vk-hash]");
      if (button) deleteVkHash(button.dataset.deleteVkHash);
    });
    $("#copy-bulk-links").addEventListener("click", copyBulkLinks);
    $("#user-search").addEventListener("input", renderUsers);
    $("#user-auto-refresh-interval").addEventListener("change", () => setUserAutoRefresh());
    $$("[data-user-sort]").forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.userSort;
      if (state.userSort.key === key) state.userSort.direction = state.userSort.direction === "asc" ? "desc" : "asc";
      else state.userSort = { key, direction: "asc" };
      renderUsers();
    }));
    $("#bulk-user-action").addEventListener("change", renderSelectedUsersControls);
    $("#apply-bulk-user-action").addEventListener("click", applyBulkUserAction);
    $("#select-all-users").addEventListener("change", (event) => {
      const query = $("#user-search").value.toLowerCase();
      state.users.filter((user) => user.role !== "admin" && JSON.stringify(user).toLowerCase().includes(query)).forEach((user) => {
        if (event.target.checked) state.selectedUsers.add(user.password);
        else state.selectedUsers.delete(user.password);
      });
      renderUsers();
    });
    $("#users-body").addEventListener("change", (event) => {
      const input = event.target.closest("[data-select-user]");
      if (!input) return;
      if (input.checked) state.selectedUsers.add(input.dataset.selectUser);
      else state.selectedUsers.delete(input.dataset.selectUser);
      renderSelectedUsersControls();
    });
    $("#users-body").addEventListener("click", async (event) => {
      const button = event.target.closest("button"); if (!button) return;
      if (button.dataset.actionsToggle) {
        const user = state.users.find((item) => item.password === button.dataset.actionsToggle);
        openUserActions(button, user);
        return;
      }
      closeUserActions();
      await handleUserActionButton(button);
    });
    document.addEventListener("click", async (event) => {
      const action = event.target.closest("#user-actions-popover button");
      if (action) {
        closeUserActions();
        await handleUserActionButton(action);
        return;
      }
      if (!event.target.closest("[data-actions-toggle]") && !event.target.closest("#user-actions-popover")) {
        closeUserActions();
      }
    });
    window.addEventListener("resize", closeUserActions);
    window.addEventListener("scroll", closeUserActions, true);
    $("#load-logs").addEventListener("click", loadLogs);
    $("#download-logs").addEventListener("click", downloadLogs);
    $("#cleanup-preview").addEventListener("click", (event) => cleanupSystem(false, event.currentTarget));
    $("#cleanup-apply").addEventListener("click", (event) => cleanupSystem(true, event.currentTarget));
    $("#log-source").addEventListener("change", loadLogs);
    $("#log-limit").addEventListener("change", loadLogs);
    $("#log-filter").addEventListener("change", renderLogs);
    $$('[data-service]').forEach((button) => button.addEventListener("click", () => serviceAction(button.dataset.service, button)));
    $("#load-backups").addEventListener("click", loadBackups);
    $("#create-full-backup").addEventListener("click", () => createBackup("full"));
    $("#create-users-backup").addEventListener("click", () => createBackup("users"));
    $("#save-backup-schedule").addEventListener("click", saveBackupSchedule);
    $("#save-autoclean").addEventListener("click", saveAutoclean);
    $("#run-autoclean").addEventListener("click", runAutoclean);
    $("#save-telegram").addEventListener("click", saveTelegramSettings);
    $("#test-telegram").addEventListener("click", testTelegramSettings);
    $("#install-tailscale").addEventListener("click", installTailscale);
    $("#connect-tailscale").addEventListener("click", connectTailscale);
    $("#disconnect-tailscale").addEventListener("click", disconnectTailscale);
    $("#logout-tailscale").addEventListener("click", logoutTailscale);
    $("#check-tailscale").addEventListener("click", checkTailscale);
    $("#check-panel-update").addEventListener("click", () => checkPanelUpdate(true));
    $("#update-panel").addEventListener("click", updatePanel);
    $("#renew-certificate").addEventListener("click", renewCertificate);
    $("#download-certificate").addEventListener("click", downloadCertificate);
    $("#change-domain").addEventListener("click", changeDomain);
    $("#save-wdtt-settings").addEventListener("click", saveWdttSettings);
    $("#upload-backup").addEventListener("click", () => $("#backup-upload").click());
    $("#backup-upload").addEventListener("change", (event) => uploadBackup(event.target.files[0]));
    $("#backups-list").addEventListener("click", (event) => {
      const restore = event.target.closest("[data-restore]"); if (restore) restoreBackup(restore.dataset.restore, restore.dataset.backupType);
      const download = event.target.closest("[data-download-backup]"); if (download) downloadBackup(download.dataset.downloadBackup);
      const deleted = event.target.closest("[data-delete-backup]"); if (deleted) deleteBackup(deleted.dataset.deleteBackup);
    });
    $$("dialog button[value='cancel']").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
    $("#logout-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await fetch(`${BASE}logout`, { method: "POST", headers: { "X-CSRF-Token": CSRF } });
      location.reload();
    });
    window.addEventListener("resize", () => { if (state.overview) loadHistory().catch(() => {}); });
  }

  restoreSidebarState();
  restoreTheme();
  restoreAccent();
  restoreActiveTab();
  bindEvents();
  restoreUserAutoRefresh();
  Promise.all([loadOverview(), loadUsers(), loadVkHashes(), loadPanelVersion()]).catch((error) => toast(error.message, true));
  setInterval(() => loadOverview().catch(() => {}), 10000);
})();
