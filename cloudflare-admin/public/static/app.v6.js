// CF D1 admin — build 6-seal
// /admin on custom domain, or root on workers.dev
const API_BASE = (() => {
  const p = location.pathname || "";
  if (p === "/admin" || p.startsWith("/admin/")) return "/admin/api";
  return "/api";
})();

// Drop legacy Windows admin token so it cannot poison CF session
try {
  localStorage.removeItem("tts_admin_token");
} catch (_) {}

const state = {
  token: localStorage.getItem("tts_cf_admin") || "",
  page: "accounts",
  onlineTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function toast(msg) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3200);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function fmtM(n) {
  const v = Number(n);
  if (v <= 0 || v === -1) return "∞ Unlimited";
  if (v >= 1e6) return (v / 1e6).toFixed(v % 1e6 === 0 ? 0 : 1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(v % 1e3 === 0 ? 0 : 1) + "K";
  return String(v);
}

function badge(status) {
  return `<span class="badge ${esc(status)}">${esc(status)}</span>`;
}

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (state.token) {
    headers["X-Admin-Token"] = state.token;
    headers["Authorization"] = `Bearer ${state.token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers,
    credentials: "same-origin",
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text || res.statusText };
  }
  if (!res.ok) {
    const msg = data.detail || data.error || res.statusText;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}

function isAuthError(err) {
  if (!err) return false;
  if (err.status === 401) return true;
  const m = String(err.message || "").toLowerCase();
  return (
    m === "wrong password" ||
    m.includes("admin auth required") ||
    m.includes("session expired") ||
    m.includes("expired session")
  );
}

function showLogin() {
  if (typeof window.__ttsShowLogin === "function") window.__ttsShowLogin();
  else {
    const login = $("#login-view");
    const app = $("#app-view");
    if (login) {
      login.hidden = false;
      login.style.display = "grid";
    }
    if (app) {
      app.hidden = true;
      app.style.display = "none";
    }
  }
  const hint = $("#boot-hint");
  if (hint) hint.textContent = "Enter admin password to continue";
}

function showApp() {
  if (typeof window.__ttsShowApp === "function") window.__ttsShowApp();
  else {
    const login = $("#login-view");
    const app = $("#app-view");
    if (login) {
      login.hidden = true;
      login.style.display = "none";
    }
    if (app) {
      app.hidden = false;
      app.style.display = "block";
    }
  }
}

async function login(e) {
  e.preventDefault();
  const errEl = $("#login-error");
  if (errEl) errEl.textContent = "";
  try {
    state.token = "";
    localStorage.removeItem("tts_cf_admin");
    const password = $("#password").value;
    const data = await api("/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    if (!data.token) throw new Error("login ok but no token returned");
    state.token = data.token;
    localStorage.setItem("tts_cf_admin", state.token);
    showApp();
    await navigate("accounts");
  } catch (err) {
    state.token = "";
    localStorage.removeItem("tts_cf_admin");
    showLogin();
    if (errEl) errEl.textContent = err.message || "Login failed";
  }
}

async function logout() {
  try {
    await api("/logout", { method: "POST" });
  } catch (_) {}
  state.token = "";
  localStorage.removeItem("tts_cf_admin");
  showLogin();
}

function setNav(page) {
  state.page = page;
  $$(".nav-btn[data-page]").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === page)
  );
  const titles = {
    overview: ["Overview", "Tổng quan D1 · accounts · proxy pool · gói · online gen"],
    accounts: ["Accounts", "Username/password · gói ký tự · max luồng (1–5) · proxyxoay"],
    online: ["Online gen", "User đang gen TTS audio (heartbeat ~2 phút)"],
    proxies: ["Proxy Pool", "Proxyxoay rotating lines"],
    packages: ["Gói ký tự", "Gói theo triệu ký tự gán cho account"],
  };
  const t = titles[page] || [page, ""];
  const title = $("#page-title");
  const sub = $("#page-sub");
  if (title) title.textContent = t[0];
  if (sub) sub.textContent = t[1];
}

async function refreshChip() {
  try {
    const d = await api("/dashboard");
    const chip = $("#dash-chip");
    if (chip) {
      const on = Number(d.online_gen != null ? d.online_gen : d.online) || 0;
      chip.textContent = `${d.accounts || 0} accounts · ${on} online gen · ${d.proxies_ready || 0} proxies`;
    }
    const pill = $("#live-pill");
    if (pill) {
      const on = Number(d.online_gen != null ? d.online_gen : d.online) || 0;
      pill.textContent = on > 0 ? `● ${on} gen` : "● D1 live";
    }
  } catch (_) {}
}

function clearOnlineTimer() {
  if (state.onlineTimer) {
    clearInterval(state.onlineTimer);
    state.onlineTimer = null;
  }
}

function fmtDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return `${m}m ${r}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

async function navigate(page) {
  if (!page) return;
  clearOnlineTimer();
  setNav(page);
  const root = $("#content");
  if (!root) return;
  root.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    if (page === "overview") await renderOverview(root);
    else if (page === "accounts") await renderAccounts(root);
    else if (page === "online") await renderOnline(root);
    else if (page === "proxies") await renderProxies(root);
    else if (page === "packages") await renderPackages(root);
    await refreshChip();
  } catch (err) {
    console.error("navigate error", page, err);
    if (isAuthError(err)) {
      state.token = "";
      localStorage.removeItem("tts_cf_admin");
      showLogin();
      const errEl = $("#login-error");
      if (errEl) errEl.textContent = "Session hết hạn — đăng nhập lại";
      return;
    }
    root.innerHTML = `<p class="error">${esc(err.message)}</p>
      <p class="muted">Thử Refresh. Hard refresh (Ctrl+Shift+R) nếu CSS cũ.</p>`;
  }
}

async function renderOverview(root) {
  const [d, acc, pxs, pkgs] = await Promise.all([
    api("/dashboard"),
    api("/accounts"),
    api("/proxies"),
    api("/packages"),
  ]);
  const accounts = (acc && acc.accounts) || [];
  const proxies = (pxs && pxs.proxies) || [];
  const packages = (pkgs && pkgs.packages) || [];
  // never assume d.usage exists (legacy Windows JS crashed here)
  const st = (d && d.usage && d.usage.jobs_by_status) || {};
  void st;
  const usedSum = accounts.reduce((s, a) => s + (Number(a.chars_used) || 0), 0);
  const quotaSum = accounts.reduce((s, a) => s + (Number(a.char_quota) || 0), 0);

  const onlineN = Number(d.online_gen != null ? d.online_gen : d.online) || 0;
  root.innerHTML = `
    <div class="cards">
      <div class="card"><div class="k">Accounts</div><div class="v">${d.accounts || 0}</div><div class="hint">tenants on D1</div></div>
      <div class="card"><div class="k">Online gen</div><div class="v ${onlineN ? "ok" : ""}">${onlineN}</div><div class="hint">đang gen TTS (≤2 phút)</div></div>
      <div class="card"><div class="k">Proxy ready</div><div class="v ok">${d.proxies_ready || 0}<span style="font-size:0.9rem;color:var(--muted)">/${proxies.length}</span></div><div class="hint">enabled lines</div></div>
      <div class="card"><div class="k">Gói</div><div class="v">${packages.length}</div><div class="hint">character packages</div></div>
      <div class="card"><div class="k">Used / Quota</div><div class="v">${fmtM(usedSum)}<span style="font-size:0.9rem;color:var(--muted)">/${fmtM(quotaSum)}</span></div><div class="hint">all accounts</div></div>
    </div>
    <div class="panel">
      <h3>Accounts gần đây</h3>
      <table>
        <thead><tr><th>User</th><th>Role</th><th>Gói</th><th>Used / Quota</th><th>Luồng</th><th>Proxy</th></tr></thead>
        <tbody>
          ${accounts
            .slice(0, 8)
            .map(
              (a) => `
            <tr>
              <td>${esc(a.username)}</td>
              <td>${badge(a.role || "user")}</td>
              <td>${esc(a.package_name || "—")}</td>
              <td>${
                Number(a.char_quota) <= 0 || a.unlimited
                  ? `${Number(a.chars_used || 0).toLocaleString()} / ∞`
                  : `${fmtM(a.chars_used)} / ${fmtM(a.char_quota)}`
              }</td>
              <td>${a.max_workers ?? "—"}</td>
              <td class="mono">${a.has_proxy ? esc(a.proxy_host || a.proxy_id || "yes") : "—"}</td>
            </tr>`
            )
            .join("") || `<tr><td colspan="6" class="muted">Chưa có account</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>Proxy pool</h3>
      <table>
        <thead><tr><th>ID</th><th>Label</th><th>Host</th><th>User</th><th>On</th></tr></thead>
        <tbody>
          ${proxies
            .map(
              (p) => `
            <tr>
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.label || "")}</td>
              <td class="mono">${esc(p.host)}:${esc(p.port)}</td>
              <td>${esc(p.username || "")}</td>
              <td>${p.enabled ? badge("ready") : badge("dead")}</td>
            </tr>`
            )
            .join("") || `<tr><td colspan="5" class="muted">Chưa có proxy</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>Gói ký tự</h3>
      <table>
        <thead><tr><th>ID</th><th>Tên</th><th>Ký tự</th></tr></thead>
        <tbody>
          ${packages
            .map(
              (p) => `
            <tr>
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.name)}</td>
              <td>${fmtM(p.chars)} <span class="muted">(${Number(p.chars).toLocaleString()})</span></td>
            </tr>`
            )
            .join("") || `<tr><td colspan="3" class="muted">Chưa có gói</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

async function renderOnline(root) {
  const paint = async () => {
    const data = await api("/online");
    const list = data.online || [];
    const ttl = data.ttl || 120;
    root.innerHTML = `
      <div class="cards">
        <div class="card">
          <div class="k">Đang gen TTS</div>
          <div class="v ${list.length ? "ok" : ""}">${list.length}</div>
          <div class="hint">heartbeat TTL ${ttl}s · auto refresh 10s</div>
        </div>
        <div class="card">
          <div class="k">Tổng workers</div>
          <div class="v">${list.reduce((s, r) => s + (Number(r.workers) || 0), 0)}</div>
          <div class="hint">luồng khai báo từ studio</div>
        </div>
      </div>
      <div class="panel">
        <h3>Online gen <span class="muted" style="font-weight:400;font-size:13px">— user đang tạo audio</span></h3>
        <p class="muted" style="margin-top:-0.5rem">
          Studio gửi <code>start</code> khi bấm Gen, <code>heartbeat</code> ~20s, <code>stop</code> khi xong.
          Mất heartbeat &gt; ${ttl}s → coi như offline.
        </p>
        <table>
          <thead>
            <tr>
              <th>User</th><th>Loại</th><th>Luồng</th><th>Tiến độ</th>
              <th>Thời gian</th><th>Heartbeat</th><th>Label</th>
            </tr>
          </thead>
          <tbody>
            ${
              list
                .map((r) => {
                  const done =
                    (Number(r.ok_chunks) || 0) + (Number(r.fail_chunks) || 0);
                  const total = Number(r.total_chunks) || 0;
                  const prog =
                    r.progress_pct != null
                      ? `${r.progress_pct}% · ${done}/${total || "?"} (ok ${r.ok_chunks || 0} / fail ${r.fail_chunks || 0})`
                      : total
                        ? `${done}/${total}`
                        : "…";
                  return `<tr>
                    <td><strong>${esc(r.username)}</strong>
                      <div class="muted mono" style="font-size:11px">${esc(r.account_id || "")}</div>
                    </td>
                    <td>${badge(r.kind || "preview")}</td>
                    <td>${Number(r.workers) || 0}</td>
                    <td>${esc(prog)}</td>
                    <td>${r.duration_s != null ? fmtDuration(r.duration_s) : "—"}</td>
                    <td class="${r.last_seen_ago_s > 60 ? "error" : "muted"}">${r.last_seen_ago_s != null ? r.last_seen_ago_s + "s trước" : "—"}</td>
                    <td class="muted" style="font-size:12px">${esc(r.label || r.client || "")}</td>
                  </tr>`;
                })
                .join("") ||
              `<tr><td colspan="7" class="muted">Chưa có ai đang gen. Mở studio → login account CF → bấm Gen.</td></tr>`
            }
          </tbody>
        </table>
      </div>
    `;
  };

  await paint();
  clearOnlineTimer();
  state.onlineTimer = setInterval(() => {
    if (state.page !== "online") {
      clearOnlineTimer();
      return;
    }
    paint().catch((e) => {
      if (isAuthError(e)) {
        clearOnlineTimer();
        state.token = "";
        localStorage.removeItem("tts_cf_admin");
        showLogin();
      }
    });
    refreshChip().catch(() => {});
  }, 10000);
}

async function renderAccounts(root) {
  const [acc, pkgs, pxs] = await Promise.all([
    api("/accounts"),
    api("/packages"),
    api("/proxies"),
  ]);
  const accountsList = acc.accounts || [];
  const packagesList = pkgs.packages || [];
  const pkgOpts = packagesList
    .map((p) => {
      const c = Number(p.chars);
      const lab =
        c <= 0 || c === -1
          ? `${esc(p.name)} (∞ Unlimited)`
          : `${esc(p.name)} (${fmtM(p.chars)})`;
      return `<option value="${esc(p.id)}">${lab}</option>`;
    })
    .join("");
  const pxOpts = (pxs.proxies || [])
    .filter((p) => p.enabled)
    .map((p) => {
      const prov = p.provider || "proxyxoay_net";
      const hint =
        prov.includes("shop")
          ? "shop key"
          : `${p.host || "?"}:${p.port || "?"}`;
      return `<option value="${esc(p.id)}">${esc(p.label || p.id)} · ${esc(prov)} · ${esc(hint)}</option>`;
    })
    .join("");

  root.innerHTML = `
    <div class="panel" id="a-form-panel">
      <h3 id="a-form-title">Tạo account</h3>
      <p class="muted" id="a-form-hint">
        Click 1 hàng account bên dưới để <strong>sửa</strong> (gói / luồng / max chars / mật khẩu).
        Form trống = tạo mới.
      </p>
      <input type="hidden" id="a-edit-id" value="" />
      <div class="grid-2">
        <div class="field"><label>Username</label><input id="a-user" autocomplete="off" /></div>
        <div class="field">
          <label>Password <span class="muted" id="a-pass-hint">(bắt buộc khi tạo)</span></label>
          <input id="a-pass" type="password" autocomplete="new-password" placeholder="" />
        </div>
        <div class="field">
          <label>Role</label>
          <select id="a-role"><option value="user">user</option><option value="admin">admin</option></select>
        </div>
        <div class="field">
          <label>Gói ký tự</label>
          <select id="a-pkg"><option value="">— chọn gói —</option>${pkgOpts}</select>
        </div>
        <div class="field"><label>Max luồng (1–5)</label><input id="a-mw" type="number" min="1" max="5" value="3" /></div>
        <div class="field"><label>Max chars/chunk (0=mặc định 300)</label><input id="a-mc" type="number" min="0" max="5000" value="0" /></div>
        <div class="field">
          <label>Cách split chunk</label>
          <select id="a-split">
            <option value="line">Theo dòng (paragraph / dòng → câu)</option>
            <option value="chars">Theo max chars (full cửa sổ, cắt tại , .)</option>
          </select>
        </div>
        <div class="field">
          <label>Gắn proxy (chỉ khi tạo mới)</label>
          <select id="a-px"><option value="">— không gắn —</option>${pxOpts}</select>
        </div>
        <div class="field">
          <label>Enabled</label>
          <select id="a-enabled"><option value="1">Bật</option><option value="0">Tắt</option></select>
        </div>
      </div>
      <div class="form-actions" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        <button class="primary" type="button" id="a-save">Tạo account</button>
        <button type="button" id="a-cancel" class="hidden">Hủy / form mới</button>
        <button type="button" id="a-reset-used" class="hidden">Reset used</button>
        <span class="muted" id="a-edit-label" style="font-size:12px"></span>
      </div>
      <div id="a-new" class="hidden" style="margin-top:1rem"></div>
    </div>
    <div class="panel">
      <h3>Accounts <span class="muted" style="font-weight:400;font-size:13px">— click hàng để edit form trên</span></h3>
      <table>
        <thead>
          <tr>
            <th>User</th><th>Role</th><th>Online</th><th>Gói</th><th>Used / Quota</th>
            <th>Luồng</th><th>Max chars</th><th>Split</th><th>Proxies gắn</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${accountsList
            .map((a) => {
              const proxies = a.proxies || [];
              const proxyListHtml = proxies.length > 0
                ? proxies.map(p => `
                  <div class="proxy-tag" style="display:flex;align-items:center;gap:4px;margin:2px 0" data-stop-row="1">
                    <span style="font-size:11px">${esc(p.priority)}. ${esc(p.label || p.proxy_id)} (${esc((p.provider || "").replace("proxyxoay_", ""))})</span>
                    <button type="button" class="danger" style="padding:2px 6px;font-size:10px" data-act="remove-proxy" data-proxy-id="${esc(p.proxy_id)}" data-stop-row="1">×</button>
                  </div>
                `).join("")
                : `<span class="muted" style="font-size:11px">chưa gắn proxy</span>`;
              const onlineBadge = a.online
                ? `<span class="badge ok">GEN</span>${
                    a.gen_online
                      ? `<div class="muted" style="font-size:10px">${esc(a.gen_online.kind || "")} · ${Number(a.gen_online.workers) || 0}w</div>`
                      : ""
                  }`
                : `<span class="muted" style="font-size:11px">—</span>`;
              const splitLab =
                String(a.split_mode || "line").toLowerCase() === "chars"
                  ? "chars"
                  : "line";
              return `
            <tr data-id="${esc(a.id)}" class="account-row" style="cursor:pointer" title="Click để sửa">
              <td><strong>${esc(a.username)}</strong></td>
              <td>${badge(a.role || "user")}</td>
              <td>${onlineBadge}</td>
              <td>${esc(a.package_name || "—")}</td>
              <td>${
                Number(a.char_quota) <= 0 || a.unlimited
                  ? `${Number(a.chars_used || 0).toLocaleString()} / ∞`
                  : `${fmtM(a.chars_used)} / ${fmtM(a.char_quota)}`
              }</td>
              <td>${a.max_workers ?? 2}</td>
              <td>${a.max_chars ?? 0}</td>
              <td><span class="badge ${splitLab === "chars" ? "user" : ""}">${esc(splitLab)}</span></td>
              <td data-stop-row="1">
                ${proxyListHtml}
                <button type="button" style="margin-top:4px;font-size:11px;padding:4px 8px" data-act="add-proxy" data-stop-row="1">+ Thêm proxy</button>
              </td>
              <td class="row" style="margin:0;flex-wrap:wrap;gap:4px" data-stop-row="1">
                <button type="button" data-act="edit" data-stop-row="1">Sửa</button>
                <button type="button" data-act="reset" data-stop-row="1">Reset used</button>
                <button type="button" class="danger" data-act="del" data-stop-row="1">Xóa</button>
              </td>
            </tr>`;
            })
            .join("") || `<tr><td colspan="10" class="muted">Chưa có account</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  const byId = Object.fromEntries(accountsList.map((a) => [a.id, a]));

  function setCreateMode() {
    $("#a-edit-id").value = "";
    $("#a-form-title").textContent = "Tạo account";
    $("#a-pass-hint").textContent = "(bắt buộc khi tạo)";
    $("#a-pass").placeholder = "";
    $("#a-user").value = "";
    $("#a-user").disabled = false;
    $("#a-pass").value = "";
    $("#a-role").value = "user";
    $("#a-pkg").value = "";
    $("#a-mw").value = "3";
    $("#a-mc").value = "0";
    if ($("#a-split")) $("#a-split").value = "line";
    $("#a-px").value = "";
    $("#a-px").disabled = false;
    $("#a-enabled").value = "1";
    $("#a-save").textContent = "Tạo account";
    $("#a-cancel").classList.add("hidden");
    $("#a-reset-used").classList.add("hidden");
    $("#a-edit-label").textContent = "";
    root.querySelectorAll("tr.account-row").forEach((r) => r.classList.remove("row-selected"));
  }

  function setEditMode(a) {
    if (!a) return;
    $("#a-edit-id").value = a.id || "";
    $("#a-form-title").textContent = `Sửa account · ${a.username || ""}`;
    $("#a-pass-hint").textContent = "(để trống = giữ mật khẩu cũ)";
    $("#a-pass").placeholder = "••••••••";
    $("#a-user").value = a.username || "";
    $("#a-user").disabled = true; // username không đổi qua PATCH
    $("#a-pass").value = "";
    $("#a-role").value = a.role === "admin" ? "admin" : "user";
    $("#a-pkg").value = a.package_id || "";
    $("#a-mw").value = String(a.max_workers ?? 2);
    $("#a-mc").value = String(a.max_chars ?? 0);
    if ($("#a-split"))
      $("#a-split").value =
        String(a.split_mode || "line").toLowerCase() === "chars" ? "chars" : "line";
    $("#a-px").value = "";
    $("#a-px").disabled = true; // proxy gắn/gỡ ở cột bảng
    $("#a-enabled").value = a.enabled === false || a.enabled === 0 ? "0" : "1";
    $("#a-save").textContent = "Lưu thay đổi";
    $("#a-cancel").classList.remove("hidden");
    $("#a-reset-used").classList.remove("hidden");
    const used =
      Number(a.char_quota) <= 0 || a.unlimited
        ? `${Number(a.chars_used || 0).toLocaleString()} / ∞`
        : `${fmtM(a.chars_used)} / ${fmtM(a.char_quota)}`;
    $("#a-edit-label").textContent = `Đang sửa · used ${used}`;
    root.querySelectorAll("tr.account-row").forEach((r) => {
      r.classList.toggle("row-selected", r.dataset.id === a.id);
    });
    // scroll form into view
    const panel = $("#a-form-panel");
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("#a-cancel").onclick = () => setCreateMode();

  $("#a-reset-used").onclick = async () => {
    const id = $("#a-edit-id").value;
    if (!id) return;
    try {
      await api(`/accounts/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ chars_used: 0 }),
      });
      toast("Reset used");
      navigate("accounts");
    } catch (e) {
      toast(e.message);
    }
  };

  $("#a-save").onclick = async () => {
    const editId = ($("#a-edit-id").value || "").trim();
    try {
      let mw = +($("#a-mw").value || 3);
      if (mw < 1) mw = 1;
      if (mw > 5) mw = 5;
      let mc = +($("#a-mc").value || 0);
      if (mc < 0) mc = 0;
      if (mc > 5000) mc = 5000;

      if (!editId) {
        // CREATE
        const body = {
          username: $("#a-user").value.trim(),
          password: $("#a-pass").value,
          role: $("#a-role").value,
          package_id: $("#a-pkg").value,
          max_workers: mw,
          max_chars: mc,
          split_mode: ($("#a-split") && $("#a-split").value) || "line",
          proxy_id: $("#a-px").value,
        };
        if (!body.username || !body.password) {
          toast("Cần username + password");
          return;
        }
        const res = await api("/accounts", {
          method: "POST",
          body: JSON.stringify(body),
        });
        const box = $("#a-new");
        box.classList.remove("hidden");
        box.style.display = "block";
        const mcLabel = Number(res.max_chars) > 0 ? res.max_chars : "mặc định";
        box.innerHTML = `<p class="muted">Account <strong>${esc(res.username)}</strong> · gói ${fmtM(res.char_quota)} · max ${res.max_workers} luồng · chunk ${esc(String(mcLabel))}</p>
          <pre class="keybox">${esc(res.api_key || "")}</pre>
          <p class="muted">Copy api_key ngay — chỉ hiện 1 lần</p>
          <button type="button" id="a-copy">Copy key</button>`;
        if (res.api_key) {
          $("#a-copy").onclick = () => {
            navigator.clipboard.writeText(res.api_key);
            toast("Copied");
          };
        }
        toast("Account created");
        setTimeout(() => navigate("accounts"), 800);
        return;
      }

      // UPDATE — always send package_id so gói change is never skipped
      const body = {
        role: $("#a-role").value,
        package_id: ($("#a-pkg").value || "").trim(),
        max_workers: mw,
        max_chars: mc,
        split_mode: ($("#a-split") && $("#a-split").value) || "line",
        enabled: $("#a-enabled").value === "1",
      };
      const pw = $("#a-pass").value;
      if (pw) body.password = pw;

      const updated = await api(`/accounts/${editId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      toast(
        updated.package_name
          ? `Đã lưu · gói ${updated.package_name} · ${mw} luồng`
          : "Đã lưu account (gói / luồng / max chars / …)"
      );
      navigate("accounts");
    } catch (e) {
      toast(e.message);
    }
  };

  root.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    const account = byId[id];

    // Click row → fill form trên
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-stop-row]")) return;
      setEditMode(account);
    });

    const editBtn = tr.querySelector('[data-act="edit"]');
    if (editBtn)
      editBtn.onclick = (ev) => {
        ev.stopPropagation();
        setEditMode(account);
      };

    const resetBtn = tr.querySelector('[data-act="reset"]');
    if (resetBtn)
      resetBtn.onclick = async (ev) => {
        ev.stopPropagation();
        try {
          await api(`/accounts/${id}`, {
            method: "PATCH",
            body: JSON.stringify({ chars_used: 0 }),
          });
          toast("Reset used");
          navigate("accounts");
        } catch (e) {
          toast(e.message);
        }
      };
    const delBtn = tr.querySelector('[data-act="del"]');
    if (delBtn)
      delBtn.onclick = async (ev) => {
        ev.stopPropagation();
        if (!confirm("Xóa account?")) return;
        try {
          await api(`/accounts/${id}`, { method: "DELETE" });
          toast("Deleted");
          navigate("accounts");
        } catch (e) {
          toast(e.message);
        }
      };
    
    // Thêm proxy
    const addProxyBtn = tr.querySelector('[data-act="add-proxy"]');
    if (addProxyBtn)
      addProxyBtn.onclick = async (ev) => {
        ev.stopPropagation();
        // Lấy danh sách proxies chưa gắn
        const pxsData = await api("/proxies");
        const allProxies = (pxsData.proxies || []).filter(p => p.enabled);
        const attachedProxyIds = new Set(
          Array.from(tr.querySelectorAll('[data-proxy-id]')).map(el => el.dataset.proxyId)
        );
        const availableProxies = allProxies.filter(p => !attachedProxyIds.has(p.id));
        
        if (availableProxies.length === 0) {
          toast("Không có proxy khả dụng để gắn");
          return;
        }
        
        const options = availableProxies.map(p => 
          `<option value="${esc(p.id)}">${esc(p.label || p.id)} (${esc((p.provider || "").replace("proxyxoay_", ""))})</option>`
        ).join("");
        
        // Hiển thị dropdown trong modal đơn giản
        const modal = document.createElement("div");
        modal.style.cssText = "position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
        modal.innerHTML = `
          <div style="background:var(--panel-solid,#121a2b);padding:20px;border-radius:8px;min-width:300px;border:1px solid var(--border)">
            <h4 style="margin:0 0 12px 0">Chọn proxy để gắn</h4>
            <select id="modal-proxy-select" style="width:100%;padding:8px;margin-bottom:12px">
              ${options}
            </select>
            <div style="display:flex;gap:8px;justify-content:flex-end">
              <button id="modal-cancel" style="padding:8px 16px">Hủy</button>
              <button id="modal-confirm" class="primary" style="padding:8px 16px">Gắn</button>
            </div>
          </div>
        `;
        document.body.appendChild(modal);
        
        modal.querySelector("#modal-cancel").onclick = () => modal.remove();
        modal.querySelector("#modal-confirm").onclick = async () => {
          const selectedProxyId = modal.querySelector("#modal-proxy-select").value;
          modal.remove();
          try {
            await api(`/accounts/${id}/proxies`, {
              method: "POST",
              body: JSON.stringify({ proxy_id: selectedProxyId }),
            });
            toast("Đã gắn proxy");
            navigate("accounts");
          } catch (e) {
            toast(e.message);
          }
        };
      };

    // Xóa proxy
    tr.querySelectorAll('[data-act="remove-proxy"]').forEach((btn) => {
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        const proxyId = btn.dataset.proxyId;
        if (!confirm("Gỡ proxy này khỏi account?")) return;
        try {
          await api(`/accounts/${id}/proxies/${proxyId}`, { method: "DELETE" });
          toast("Đã gỡ proxy");
          navigate("accounts");
        } catch (e) {
          toast(e.message);
        }
      };
    });
  });
}

async function renderProxies(root) {
  const data = await api("/proxies");
  const list = data.proxies || [];
  const ready = list.filter((p) => p.enabled).length;
  const byId = Object.fromEntries(list.map((p) => [p.id, p]));

  root.innerHTML = `
    <div class="panel" id="p-form-panel">
      <h3 id="p-form-title">Thêm proxy</h3>
      <p class="muted" id="p-form-hint">
        Click <strong>Sửa</strong> trên 1 hàng để sửa key/host/label.
        API key / password để trống khi sửa = <strong>giữ giá trị cũ</strong>.
      </p>
      <div class="grid-2">
        <div class="field"><label>ID</label><input id="p-id" placeholder="auto nếu trống" /></div>
        <div class="field"><label>Label</label><input id="p-label" placeholder="EU #1 / Shop FPT" /></div>
        <div class="field">
          <label>Provider</label>
          <select id="p-prov">
            <option value="proxyxoay_net">proxyxoay.net</option>
            <option value="proxyxoay_shop">proxyxoay.shop</option>
          </select>
        </div>
        <div class="field">
          <label>API key <span class="muted" id="p-key-hint"></span></label>
          <input id="p-key" placeholder="key mua hàng / rotating key" autocomplete="off" />
        </div>
        <div class="field" data-net-only><label>Username</label><input id="p-user" /></div>
        <div class="field" data-net-only>
          <label>Password <span class="muted" id="p-pass-hint"></span></label>
          <input id="p-pass" type="password" autocomplete="new-password" />
        </div>
        <div class="field" data-net-only><label>Host</label><input id="p-host" placeholder="vipvn7.proxyxoay.net" value="vipvn7.proxyxoay.net" /></div>
        <div class="field" data-net-only><label>Port</label><input id="p-port" type="number" value="8978" /></div>
        <div class="field" data-shop-only style="display:none"><label>Nhà mạng</label>
          <input id="p-nhamang" value="random" placeholder="random / viettel / fpt…" />
        </div>
        <div class="field" data-shop-only style="display:none"><label>Tỉnh thành</label>
          <input id="p-tinh" value="0" placeholder="0 = random" />
        </div>
        <div class="field" data-shop-only style="display:none"><label>Whitelist IPv4</label>
          <input id="p-wl" placeholder="tuỳ chọn" />
        </div>
        <div class="field">
          <label>Enabled</label>
          <select id="p-enabled"><option value="1">Bật</option><option value="0">Tắt</option></select>
        </div>
      </div>
      <div class="form-actions" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        <button class="primary" type="button" id="p-save">Thêm proxy</button>
        <button type="button" id="p-cancel" class="hidden">Hủy / form mới</button>
        <span class="muted" id="p-edit-label" style="font-size:12px"></span>
      </div>
    </div>
    <div class="panel">
      <h3>Pool (${ready}/${list.length} enabled) <span class="muted" style="font-weight:400;font-size:13px">— Sửa để đổi key</span></h3>
      <table>
        <thead><tr><th>ID</th><th>Label</th><th>Provider</th><th>Host / Key</th><th>On</th><th></th></tr></thead>
        <tbody>
          ${list
            .map(
              (p) => `
            <tr data-id="${esc(p.id)}" class="account-row" style="cursor:pointer" title="Click để sửa">
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.label || "")}</td>
              <td>${esc((p.provider || "net").replace("proxyxoay_", ""))}</td>
              <td class="mono" style="font-size:11px">${
                String(p.provider || "").includes("shop")
                  ? esc(p.api_key || "key…")
                  : esc((p.host || "") + ":" + (p.port || ""))
              }</td>
              <td>${p.enabled ? badge("ready") : badge("dead")}</td>
              <td class="row" style="margin:0;gap:4px" data-stop-row="1">
                <button type="button" data-act="edit" data-id="${esc(p.id)}" data-stop-row="1">Sửa</button>
                <button type="button" class="danger" data-del="${esc(p.id)}" data-stop-row="1">Xóa</button>
              </td>
            </tr>`
            )
            .join("") || `<tr><td colspan="6" class="muted">Trống</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  const syncProv = () => {
    const shop = ($("#p-prov").value || "").includes("shop");
    $$("[data-net-only]").forEach((el) => {
      el.style.display = shop ? "none" : "";
    });
    $$("[data-shop-only]").forEach((el) => {
      el.style.display = shop ? "" : "none";
    });
  };
  $("#p-prov").onchange = syncProv;

  function setCreateMode() {
    $("#p-form-title").textContent = "Thêm proxy";
    $("#p-form-hint").textContent =
      "proxyxoay.net (rotating host) hoặc proxyxoay.shop (get.php key). Gắn user ở tab Accounts.";
    $("#p-id").value = "";
    $("#p-id").disabled = false;
    $("#p-label").value = "";
    $("#p-prov").value = "proxyxoay_net";
    $("#p-key").value = "";
    $("#p-key").placeholder = "key mua hàng / rotating key";
    $("#p-key-hint").textContent = "";
    $("#p-user").value = "";
    $("#p-pass").value = "";
    $("#p-pass").placeholder = "";
    $("#p-pass-hint").textContent = "";
    $("#p-host").value = "vipvn7.proxyxoay.net";
    $("#p-port").value = "8978";
    if ($("#p-nhamang")) $("#p-nhamang").value = "random";
    if ($("#p-tinh")) $("#p-tinh").value = "0";
    if ($("#p-wl")) $("#p-wl").value = "";
    $("#p-enabled").value = "1";
    $("#p-save").textContent = "Thêm proxy";
    $("#p-cancel").classList.add("hidden");
    $("#p-edit-label").textContent = "";
    root.querySelectorAll("tr.account-row").forEach((r) => r.classList.remove("row-selected"));
    syncProv();
  }

  async function setEditMode(id) {
    if (!id) return;
    try {
      // full secrets for edit
      const detail = await api(`/proxies/${encodeURIComponent(id)}`);
      const p = detail.proxy || byId[id] || {};
      $("#p-form-title").textContent = `Sửa proxy · ${p.id || id}`;
      $("#p-form-hint").textContent =
        "API key / password để trống = giữ giá trị cũ. Đổi key shop nếu gặp lỗi 102.";
      $("#p-id").value = p.id || id;
      $("#p-id").disabled = true; // id không đổi
      $("#p-label").value = p.label || "";
      const prov = p.provider || "proxyxoay_net";
      $("#p-prov").value = String(prov).includes("shop")
        ? "proxyxoay_shop"
        : "proxyxoay_net";
      // never put masked key into the field
      $("#p-key").value = p.api_key || "";
      $("#p-key").placeholder = p.api_key
        ? "•••••••• (đổi nếu cần)"
        : "key mua hàng / rotating key";
      $("#p-key-hint").textContent = p.api_key
        ? "(đã load key đầy đủ)"
        : "(chưa có key)";
      $("#p-user").value = p.username || "";
      $("#p-pass").value = "";
      $("#p-pass").placeholder = p.password
        ? "•••••••• (để trống = giữ)"
        : "";
      $("#p-pass-hint").textContent = p.password ? "(đã có password)" : "";
      // shop: host/port chỉ fallback; net: rotating host
      if (String(p.provider || "").includes("shop")) {
        $("#p-host").value = p.host || "";
        $("#p-port").value = String(p.port || 0);
      } else {
        $("#p-host").value = p.host || "vipvn7.proxyxoay.net";
        $("#p-port").value = String(p.port || 8978);
      }
      if ($("#p-nhamang"))
        $("#p-nhamang").value = p.shop_nhamang || "random";
      if ($("#p-tinh"))
        $("#p-tinh").value = String(
          p.shop_tinhthanh != null ? p.shop_tinhthanh : "0"
        );
      if ($("#p-wl")) $("#p-wl").value = p.shop_whitelist || "";
      $("#p-enabled").value =
        p.enabled === false || p.enabled === 0 ? "0" : "1";
      $("#p-save").textContent = "Lưu thay đổi";
      $("#p-cancel").classList.remove("hidden");
      $("#p-edit-label").textContent = `Đang sửa · ${esc(p.id || id)}`;
      root.querySelectorAll("tr.account-row").forEach((r) => {
        r.classList.toggle("row-selected", r.dataset.id === id);
      });
      syncProv();
      const panel = $("#p-form-panel");
      if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      toast(e.message || "Không tải được proxy");
    }
  }

  $("#p-cancel").onclick = () => setCreateMode();
  setCreateMode();

  $("#p-save").onclick = async () => {
    try {
      const provider = $("#p-prov").value || "proxyxoay_net";
      const editing = $("#p-id").disabled && $("#p-id").value.trim();
      const body = {
        id: $("#p-id").value.trim() || undefined,
        label: $("#p-label").value.trim(),
        api_key: $("#p-key").value.trim(),
        username: $("#p-user").value.trim(),
        password: $("#p-pass").value,
        host: $("#p-host").value.trim(),
        port: +$("#p-port").value || 8978,
        enabled: $("#p-enabled").value === "1",
        provider,
        shop_nhamang: $("#p-nhamang") ? $("#p-nhamang").value.trim() : "random",
        shop_tinhthanh: $("#p-tinh") ? $("#p-tinh").value.trim() : 0,
        shop_whitelist: $("#p-wl") ? $("#p-wl").value.trim() : "",
        shop_method: "GET",
      };
      if (editing && !body.id) {
        toast("Thiếu ID proxy");
        return;
      }
      // create shop: require key; edit: empty = keep
      if (
        String(provider).includes("shop") &&
        !editing &&
        !body.api_key
      ) {
        toast("Shop cần API key");
        return;
      }
      await api("/proxies", { method: "POST", body: JSON.stringify(body) });
      toast(editing ? "Đã cập nhật proxy · " + provider : "Đã thêm proxy · " + provider);
      navigate("proxies");
    } catch (e) {
      toast(e.message);
    }
  };

  root.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-stop-row]")) return;
      setEditMode(id);
    });
    const editBtn = tr.querySelector('[data-act="edit"]');
    if (editBtn)
      editBtn.onclick = (ev) => {
        ev.stopPropagation();
        setEditMode(id);
      };
  });

  root.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm("Xóa proxy?")) return;
      try {
        await api(`/proxies/${b.dataset.del}`, { method: "DELETE" });
        toast("Deleted");
        navigate("proxies");
      } catch (e) {
        toast(e.message);
      }
    };
  });
}

async function renderPackages(root) {
  const data = await api("/packages");
  const list = data.packages || [];

  root.innerHTML = `
    <div class="panel">
      <h3>Thêm gói ký tự</h3>
      <p class="muted">Gói theo triệu ký tự — hoặc Unlimited (−1). Gán cho account khi tạo / sửa.</p>
      <div class="grid-2">
        <div class="field"><label>Tên</label><input id="g-name" placeholder="Gói 20 triệu" /></div>
        <div class="field"><label>Số ký tự</label><input id="g-chars" type="number" value="20000000" step="1000000" min="-1" /></div>
        <div class="field" style="grid-column:1/-1">
          <label><input type="checkbox" id="g-unlim" /> Unlimited (không giới hạn ký tự)</label>
        </div>
      </div>
      <div class="form-actions">
        <button class="primary" type="button" id="g-save">Thêm gói</button>
      </div>
    </div>
    <div class="panel">
      <h3>Danh sách gói</h3>
      <table>
        <thead><tr><th>ID</th><th>Tên</th><th>Ký tự</th><th></th></tr></thead>
        <tbody>
          ${list
            .map(
              (p) => `
            <tr>
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.name)}</td>
              <td>${
                Number(p.chars) <= 0 || Number(p.chars) === -1
                  ? "<strong>∞ Unlimited</strong>"
                  : `${fmtM(p.chars)} <span class="muted">(${Number(p.chars).toLocaleString()})</span>`
              }</td>
              <td>
                <button type="button" class="danger" data-del="${esc(p.id)}">Xóa</button>
              </td>
            </tr>`
            )
            .join("") || `<tr><td colspan="4" class="muted">Chưa có gói</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  const gUnlim = $("#g-unlim");
  if (gUnlim)
    gUnlim.onchange = () => {
      const on = gUnlim.checked;
      if ($("#g-chars")) {
        $("#g-chars").disabled = on;
        if (on) $("#g-chars").value = -1;
      }
      if (on && !$("#g-name").value.trim()) $("#g-name").value = "Unlimited";
    };

  $("#g-save").onclick = async () => {
    try {
      const unlimited = !!(gUnlim && gUnlim.checked);
      await api("/packages", {
        method: "POST",
        body: JSON.stringify({
          name: $("#g-name").value.trim() || (unlimited ? "Unlimited" : "Gói"),
          chars: unlimited ? -1 : +$("#g-chars").value || 1000000,
          unlimited,
        }),
      });
      toast(unlimited ? "Unlimited package saved" : "Package saved");
      navigate("packages");
    } catch (e) {
      toast(e.message);
    }
  };

  root.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("Xóa gói?")) return;
      try {
        await api(`/packages/${b.dataset.del}`, { method: "DELETE" });
        toast("Deleted");
        navigate("packages");
      } catch (e) {
        toast(e.message);
      }
    };
  });
}

// wire UI
const loginForm = $("#login-form");
if (loginForm) loginForm.addEventListener("submit", login);
const btnLogout = $("#btn-logout");
if (btnLogout) btnLogout.onclick = logout;
const btnRefresh = $("#btn-refresh");
if (btnRefresh) btnRefresh.onclick = () => navigate(state.page);
$$(".nav-btn[data-page]").forEach((b) => {
  b.onclick = () => navigate(b.dataset.page);
});

// boot
(async () => {
  if (!state.token) {
    showLogin();
    return;
  }
  try {
    await api("/dashboard");
    showApp();
    await navigate("accounts");
  } catch {
    state.token = "";
    localStorage.removeItem("tts_cf_admin");
    showLogin();
  }
})();

// build-12 — proxy edit form
window.__TTS_ADMIN_BUILD = "12";



// deploy-force 20260721162529

// presence-v10 20260722042429

// split-v11 20260723074126

// proxy-edit-v12 20260723112023
