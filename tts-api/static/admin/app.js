const state = {
  token: localStorage.getItem("tts_admin_token") || "",
  page: "overview",
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3200);
}

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (state.token) headers["X-Admin-Token"] = state.token;
  const res = await fetch(`/admin/api${path}`, { ...opts, headers });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) {
    const msg = data.detail || data.error || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function showLogin() {
  $("#login-view").classList.remove("hidden");
  $("#app-view").classList.add("hidden");
}

function showApp() {
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
}

async function login(e) {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const password = $("#password").value;
    const data = await api("/login", { method: "POST", body: JSON.stringify({ password }) });
    state.token = data.token;
    localStorage.setItem("tts_admin_token", state.token);
    showApp();
    await navigate("overview");
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
}

async function logout() {
  try { await api("/logout", { method: "POST" }); } catch (_) {}
  state.token = "";
  localStorage.removeItem("tts_admin_token");
  showLogin();
}

function setNav(page) {
  state.page = page;
  $$(".nav-btn[data-page]").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  const titles = {
    overview: ["Overview", "Fleet health, proxies & recent jobs"],
    keys: ["API Keys", "Create keys, quotas and concurrency limits"],
    proxies: ["Proxy Pool", "Rotating residential slots"],
    settings: ["Settings", "Global defaults for new keys & workers"],
    jobs: ["Jobs", "Queue history and failures"],
    usage: ["Usage", "Daily character & job consumption"],
  };
  const t = titles[page] || [page, ""];
  $("#page-title").textContent = t[0];
  const sub = $("#page-sub");
  if (sub) sub.textContent = t[1];
}

async function navigate(page) {
  setNav(page);
  const root = $("#content");
  root.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    if (page === "overview") await renderOverview(root);
    else if (page === "keys") await renderKeys(root);
    else if (page === "proxies") await renderProxies(root);
    else if (page === "settings") await renderSettings(root);
    else if (page === "jobs") await renderJobs(root);
    else if (page === "usage") await renderUsage(root);
  } catch (err) {
    if (String(err.message).includes("401") || /auth|session|invalid/i.test(err.message)) {
      showLogin();
      return;
    }
    root.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function badge(status) {
  return `<span class="badge ${esc(status)}">${esc(status)}</span>`;
}

async function renderOverview(root) {
  const d = await api("/dashboard");
  const st = d.usage.jobs_by_status || {};
  const px = d.proxy || {};
  const queued = (st.queued || 0) + (st.running || 0);
  root.innerHTML = `
    <div class="cards">
      <div class="card"><div class="k">API keys</div><div class="v">${d.keys_count}</div><div class="hint">active tenants</div></div>
      <div class="card"><div class="k">Proxy ready</div><div class="v ok">${px.ready ?? 0}<span style="font-size:0.9rem;color:var(--muted)">/${px.total ?? 0}</span></div><div class="hint">slots available</div></div>
      <div class="card"><div class="k">Jobs done</div><div class="v ok">${st.done || 0}</div><div class="hint">all time status</div></div>
      <div class="card"><div class="k">In flight</div><div class="v warn">${queued}</div><div class="hint">queued + running</div></div>
      <div class="card"><div class="k">Failed</div><div class="v ${st.failed ? "danger" : ""}">${st.failed || 0}</div><div class="hint">needs attention</div></div>
      <div class="card"><div class="k">Max chars</div><div class="v">${d.settings.default_max_chars}</div><div class="hint">default / request</div></div>
    </div>
    <div class="panel">
      <h3>Proxy slots</h3>
      <table>
        <thead><tr><th>ID</th><th>Label</th><th>State</th><th>Exit IP</th><th>In flight</th><th>OK on IP</th><th>Total OK</th></tr></thead>
        <tbody>
          ${(d.proxies||[]).map(p => `
            <tr>
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.label)}</td>
              <td>${badge(p.state)}</td>
              <td class="mono">${esc(p.exit_ip || "—")}</td>
              <td>${p.in_flight}</td>
              <td>${p.ok_on_ip}</td>
              <td>${p.total_ok}</td>
            </tr>`).join("") || `<tr><td colspan="7" class="muted">No proxies configured</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>Recent jobs</h3>
      <table>
        <thead><tr><th>ID</th><th>Status</th><th>Chars</th><th>Proxy</th><th>ms</th><th>Preview</th></tr></thead>
        <tbody>
          ${(d.recent_jobs||[]).map(j => `
            <tr>
              <td class="mono">${esc(j.id).slice(0,16)}…</td>
              <td>${badge(j.status)}</td>
              <td>${j.text_chars}</td>
              <td class="mono">${esc(j.proxy_id || "—")}</td>
              <td>${j.duration_ms ?? "—"}</td>
              <td>${esc(j.text_preview || "")}</td>
            </tr>`).join("") || `<tr><td colspan="6" class="muted">No jobs yet</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

async function renderKeys(root) {
  const data = await api("/keys");
  root.innerHTML = `
    <div class="panel">
      <h3>Create API key</h3>
      <div class="grid-2">
        <div class="field"><label>Name</label><input id="k-name" value="customer" /></div>
        <div class="field"><label>Max chars / request</label><input id="k-max" type="number" placeholder="default" /></div>
        <div class="field"><label>Quota chars / day</label><input id="k-qc" type="number" placeholder="default" /></div>
        <div class="field"><label>Quota jobs / day</label><input id="k-qj" type="number" placeholder="default" /></div>
        <div class="field"><label>Max concurrent</label><input id="k-mc" type="number" placeholder="default" /></div>
        <div class="field"><label>Note</label><input id="k-note" placeholder="optional" /></div>
      </div>
      <div class="form-actions">
        <button class="primary" id="k-create">Create key</button>
      </div>
      <div id="k-new" class="hidden" style="margin-top:1rem"></div>
    </div>
    <div class="panel">
      <h3>Keys</h3>
      <table>
        <thead>
          <tr>
            <th>Name</th><th>Prefix</th><th>Enabled</th><th>Max chars</th>
            <th>Quota chars/day</th><th>Used today</th><th>Jobs today</th><th>Total</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${data.keys.map(k => `
            <tr data-id="${k.id}">
              <td>${esc(k.name)}</td>
              <td class="mono">${esc(k.key_prefix)}</td>
              <td>${k.enabled ? badge("ready") : badge("dead")}</td>
              <td><input class="k-edit" data-f="max_chars" type="number" value="${k.max_chars ?? ""}" style="width:90px" /></td>
              <td><input class="k-edit" data-f="quota_chars_day" type="number" value="${k.quota_chars_day ?? ""}" style="width:100px" /></td>
              <td>${k.chars_used_day}/${k.quota_chars_day}</td>
              <td>${k.jobs_used_day}/${k.quota_jobs_day}</td>
              <td>${k.total_jobs} jobs / ${k.total_chars}c</td>
              <td class="row" style="margin:0">
                <button data-act="save">Save</button>
                <button data-act="toggle">${k.enabled ? "Disable" : "Enable"}</button>
                <button class="danger" data-act="del">Delete</button>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
  $("#k-create").onclick = async () => {
    try {
      const body = {
        name: $("#k-name").value || "customer",
        note: $("#k-note").value || "",
      };
      const max = $("#k-max").value; if (max) body.max_chars = +max;
      const qc = $("#k-qc").value; if (qc) body.quota_chars_day = +qc;
      const qj = $("#k-qj").value; if (qj) body.quota_jobs_day = +qj;
      const mc = $("#k-mc").value; if (mc) body.max_concurrent = +mc;
      const res = await api("/keys", { method: "POST", body: JSON.stringify(body) });
      const box = $("#k-new");
      box.classList.remove("hidden");
      box.innerHTML = `<p class="muted">${esc(res.note)}</p><pre class="keybox">${esc(res.key)}</pre>
        <button id="k-copy">Copy key</button>`;
      $("#k-copy").onclick = () => { navigator.clipboard.writeText(res.key); toast("Copied"); };
      toast("API key created");
      setTimeout(() => navigate("keys"), 500);
    } catch (e) { toast(e.message); }
  };
  root.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    tr.querySelector('[data-act="save"]').onclick = async () => {
      const body = {};
      tr.querySelectorAll(".k-edit").forEach((inp) => {
        if (inp.value !== "") body[inp.dataset.f] = +inp.value;
      });
      try {
        await api(`/keys/${id}`, { method: "PATCH", body: JSON.stringify(body) });
        toast("Saved");
      } catch (e) { toast(e.message); }
    };
    tr.querySelector('[data-act="toggle"]').onclick = async () => {
      const en = tr.querySelector('[data-act="toggle"]').textContent === "Enable";
      try {
        await api(`/keys/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: en }) });
        navigate("keys");
      } catch (e) { toast(e.message); }
    };
    tr.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm("Delete this key?")) return;
      try {
        await api(`/keys/${id}`, { method: "DELETE" });
        navigate("keys");
      } catch (e) { toast(e.message); }
    };
  });
}

async function renderProxies(root) {
  const data = await api("/proxies");
  root.innerHTML = `
    <div class="panel">
      <h3>Add / update proxy</h3>
      <div class="grid-2">
        <div class="field"><label>ID</label><input id="p-id" placeholder="px1" /></div>
        <div class="field"><label>Label</label><input id="p-label" placeholder="EU #1" /></div>
        <div class="field"><label>Provider</label>
          <select id="p-provider"><option value="proxyxoay_net">proxyxoay_net</option><option value="static">static</option></select>
        </div>
        <div class="field"><label>Enabled</label>
          <select id="p-en"><option value="1">Yes</option><option value="0">No</option></select>
        </div>
        <div class="field"><label>API key (proxyxoay)</label><input id="p-key" /></div>
        <div class="field"><label>Host</label><input id="p-host" /></div>
        <div class="field"><label>Port</label><input id="p-port" type="number" value="8570" /></div>
        <div class="field"><label>Username</label><input id="p-user" /></div>
        <div class="field"><label>Password</label><input id="p-pass" type="password" /></div>
      </div>
      <button class="primary" id="p-save">Save proxy</button>
    </div>
    <div class="panel">
      <h3>Pool (${data.stats.ready}/${data.stats.total} ready)</h3>
      <table>
        <thead><tr><th>ID</th><th>Label</th><th>State</th><th>Exit</th><th>In flight</th><th>OK/IP</th><th>Error</th><th></th></tr></thead>
        <tbody>
          ${data.proxies.map(p => `
            <tr>
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.label)}</td>
              <td>${badge(p.state)}</td>
              <td class="mono">${esc(p.exit_ip||"—")}</td>
              <td>${p.in_flight}</td>
              <td>${p.ok_on_ip}</td>
              <td class="muted" style="max-width:180px">${esc(p.last_error||"")}</td>
              <td class="row" style="margin:0">
                <button data-rot="${esc(p.id)}">Rotate IP</button>
                <button class="danger" data-del="${esc(p.id)}">Delete</button>
              </td>
            </tr>`).join("") || `<tr><td colspan="8" class="muted">Empty — add proxies above</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
  $("#p-save").onclick = async () => {
    try {
      await api("/proxies", {
        method: "POST",
        body: JSON.stringify({
          id: $("#p-id").value || undefined,
          label: $("#p-label").value,
          provider: $("#p-provider").value,
          enabled: $("#p-en").value === "1",
          api_key: $("#p-key").value,
          host: $("#p-host").value,
          port: +$("#p-port").value || 8570,
          username: $("#p-user").value,
          password: $("#p-pass").value,
        }),
      });
      toast("Proxy saved");
      navigate("proxies");
    } catch (e) { toast(e.message); }
  };
  root.querySelectorAll("[data-rot]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      try {
        await api(`/proxies/${b.dataset.rot}/rotate`, { method: "POST" });
        toast("Rotated");
        navigate("proxies");
      } catch (e) { toast(e.message); b.disabled = false; }
    };
  });
  root.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("Delete proxy?")) return;
      try {
        await api(`/proxies/${b.dataset.del}`, { method: "DELETE" });
        navigate("proxies");
      } catch (e) { toast(e.message); }
    };
  });
}

async function renderSettings(root) {
  const s = await api("/settings");
  const numFields = [
    ["default_max_chars", "Default max chars / request", "number"],
    ["hard_max_chars", "Hard max chars (ceiling)", "number"],
    ["default_quota_chars_day", "Default quota chars / day", "number"],
    ["default_quota_jobs_day", "Default quota jobs / day", "number"],
    ["default_max_concurrent", "Default max concurrent jobs / key", "number"],
    ["inflight_per_proxy", "In-flight jobs per proxy slot", "number"],
    ["worker_count", "Worker count (restart to fully apply)", "number"],
  ];
  const strFields = [
    ["public_base_url", "Public base URL (Cloudflare Tunnel)", "text"],
    ["default_voice", "Default voice ID", "text"],
    ["default_model", "Default model", "text"],
    ["default_lang", "Default language", "text"],
  ];
  const allFields = [...numFields, ...strFields];
  root.innerHTML = `
    <div class="panel">
      <h3>Global settings</h3>
      <p class="muted" style="margin:-0.4rem 0 1rem">
        <strong>Default max chars</strong> chỉ áp dụng cho API key <em>mới</em>.
        Key cũ giữ max_chars riêng — tick ô bên dưới để ghi đè tất cả keys.
      </p>
      <div class="grid-2">
        ${allFields.map(([k, label, type]) => `
          <div class="field">
            <label>${esc(label)}</label>
            <input id="s-${k}" type="${type}" value="${esc(s[k] ?? "")}" ${type === "number" ? 'min="1" step="1"' : ""} />
          </div>`).join("")}
        <div class="field">
          <label>New admin password (optional)</label>
          <input id="s-admin_password" type="password" placeholder="leave blank to keep" autocomplete="new-password" />
        </div>
      </div>
      <label class="row" style="cursor:pointer;color:var(--text);text-transform:none;letter-spacing:0;font-size:0.92rem;font-weight:500">
        <input type="checkbox" id="s-apply-keys" style="width:auto" />
        Apply max chars / quotas to <strong>all existing API keys</strong>
      </label>
      <div class="form-actions">
        <button class="primary" id="s-save" type="button">Save settings</button>
      </div>
      <p class="muted" id="s-status" style="margin-top:0.75rem"></p>
    </div>
    <div class="panel">
      <h3>Public API quick ref</h3>
      <pre class="keybox">POST /v1/tts
Header: X-API-Key: tts_xxx
Body: {"text":"Hello world","lang":"en"}

GET /v1/tts/{job_id}
GET /v1/tts/{job_id}/audio
GET /v1/me
GET /v1/health</pre>
    </div>
  `;
  $("#s-save").onclick = async () => {
    const body = {};
    const status = $("#s-status");
    status.textContent = "Saving…";
    for (const [k, , type] of allFields) {
      const el = $(`#s-${k}`);
      if (!el) continue;
      const v = (el.value || "").trim();
      if (v === "") continue;
      if (type === "number") {
        const n = Number(v);
        if (!Number.isFinite(n)) {
          status.textContent = `Invalid number: ${k}`;
          toast(`Invalid number: ${k}`);
          return;
        }
        body[k] = Math.trunc(n);
      } else {
        body[k] = v;
      }
    }
    const pw = ($("#s-admin_password").value || "").trim();
    if (pw) body.admin_password = pw;
    body.apply_to_all_keys = !!$("#s-apply-keys").checked;
    try {
      const res = await api("/settings", { method: "PUT", body: JSON.stringify(body) });
      const maxc = res.default_max_chars;
      const ku = res.keys_updated || 0;
      const msg = `Saved. default_max_chars=${maxc}` + (ku ? ` · updated ${ku} API key(s)` : "");
      status.textContent = msg;
      toast(msg);
      // re-render so inputs show server values
      await navigate("settings");
    } catch (e) {
      status.textContent = e.message;
      toast(e.message);
    }
  };
}

async function renderJobs(root) {
  const data = await api("/jobs?limit=80");
  root.innerHTML = `
    <div class="panel">
      <h3>Jobs</h3>
      <table>
        <thead><tr><th>ID</th><th>Status</th><th>Chars</th><th>Proxy</th><th>Exit</th><th>ms</th><th>Error</th><th>Preview</th></tr></thead>
        <tbody>
          ${data.jobs.map(j => `
            <tr>
              <td class="mono">${esc(j.id).slice(0,18)}</td>
              <td>${badge(j.status)}</td>
              <td>${j.text_chars}</td>
              <td>${esc(j.proxy_id||"—")}</td>
              <td class="mono">${esc(j.exit_ip||"—")}</td>
              <td>${j.duration_ms ?? "—"}</td>
              <td class="muted" style="max-width:160px">${esc((j.error||"").slice(0,80))}</td>
              <td>${esc(j.text_preview||"")}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function renderUsage(root) {
  const u = await api("/usage");
  root.innerHTML = `
    <div class="panel">
      <h3>Usage by day</h3>
      <table>
        <thead><tr><th>Day</th><th>Chars</th><th>OK jobs</th><th>Events</th></tr></thead>
        <tbody>
          ${(u.by_day||[]).map(d => `
            <tr><td>${esc(d.day)}</td><td>${d.chars}</td><td>${d.ok_jobs}</td><td>${d.events}</td></tr>
          `).join("") || `<tr><td colspan="4" class="muted">No usage yet</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>Jobs by status</h3>
      <pre class="keybox">${esc(JSON.stringify(u.jobs_by_status || {}, null, 2))}</pre>
    </div>
  `;
}

// boot
$("#login-form").addEventListener("submit", login);
$("#btn-logout").addEventListener("click", logout);
$$(".nav-btn[data-page]").forEach((b) => b.addEventListener("click", () => navigate(b.dataset.page)));
const btnRefresh = $("#btn-refresh");
if (btnRefresh) btnRefresh.addEventListener("click", () => navigate(state.page || "overview"));

(async () => {
  if (!state.token) {
    showLogin();
    return;
  }
  try {
    await api("/dashboard");
    showApp();
    await navigate("overview");
  } catch {
    showLogin();
  }
})();
