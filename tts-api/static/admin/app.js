const state = {
  token: localStorage.getItem("tts_admin_token") || "",
  page: "overview",
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

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (state.token) headers["X-Admin-Token"] = state.token;
  const res = await fetch(`/admin/api${path}`, { ...opts, headers });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text || res.statusText };
  }
  if (!res.ok) {
    const msg = data.detail || data.error || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function showLogin() {
  if (typeof window.__ttsShowLogin === "function") window.__ttsShowLogin();
  else {
    const login = $("#login-view");
    const app = $("#app-view");
    if (login) {
      login.hidden = false;
      login.style.display = "grid";
      login.classList.remove("hidden");
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
      app.classList.remove("hidden");
    }
  }
}

async function login(e) {
  e.preventDefault();
  const errEl = $("#login-error");
  if (errEl) errEl.textContent = "";
  try {
    const password = $("#password").value;
    const data = await api("/login", { method: "POST", body: JSON.stringify({ password }) });
    state.token = data.token;
    localStorage.setItem("tts_admin_token", state.token);
    showApp();
    await navigate("overview");
  } catch (err) {
    if (errEl) errEl.textContent = err.message;
  }
}

async function logout() {
  try {
    await api("/logout", { method: "POST" });
  } catch (_) {}
  state.token = "";
  localStorage.removeItem("tts_admin_token");
  showLogin();
}

function setNav(page) {
  state.page = page;
  $$(".nav-btn[data-page]").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  const titles = {
    overview: ["Overview", "Fleet health, proxies & recent jobs"],
    keys: ["Accounts / API Keys", "Gói ký tự · max luồng (≤5) · gắn proxyxoay"],
    proxies: ["Proxy Pool", "Proxyxoay rotating lines"],
    settings: ["Settings", "Global defaults for new keys & workers"],
    jobs: ["Jobs", "Queue history and failures"],
    usage: ["Usage", "Daily character & job consumption"],
  };
  const t = titles[page] || [page, ""];
  const title = $("#page-title");
  const sub = $("#page-sub");
  if (title) title.textContent = t[0];
  if (sub) sub.textContent = t[1];
}

async function navigate(page) {
  if (!page) return;
  setNav(page);
  const root = $("#content");
  if (!root) return;
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
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
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
          ${(d.proxies || [])
            .map(
              (p) => `
            <tr>
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.label)}</td>
              <td>${badge(p.state)}</td>
              <td class="mono">${esc(p.exit_ip || "—")}</td>
              <td>${p.in_flight}</td>
              <td>${p.ok_on_ip}</td>
              <td>${p.total_ok}</td>
            </tr>`
            )
            .join("") || `<tr><td colspan="7" class="muted">No proxies configured</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>Recent jobs</h3>
      <table>
        <thead><tr><th>ID</th><th>Status</th><th>Chars</th><th>Proxy</th><th>ms</th><th>Preview</th></tr></thead>
        <tbody>
          ${(d.recent_jobs || [])
            .map(
              (j) => `
            <tr>
              <td class="mono">${esc(j.id).slice(0, 16)}…</td>
              <td>${badge(j.status)}</td>
              <td>${j.text_chars}</td>
              <td class="mono">${esc(j.proxy_id || "—")}</td>
              <td>${j.duration_ms ?? "—"}</td>
              <td>${esc(j.text_preview || "")}</td>
            </tr>`
            )
            .join("") || `<tr><td colspan="6" class="muted">No jobs yet</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function fmtM(n) {
  const v = Number(n) || 0;
  if (v >= 1e6) return (v / 1e6).toFixed(v % 1e6 === 0 ? 0 : 1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(v % 1e3 === 0 ? 0 : 1) + "K";
  return String(v);
}

function readProxyFields(prefix) {
  const host = $(`#${prefix}-phost`)?.value?.trim() || "";
  const user = $(`#${prefix}-puser`)?.value?.trim() || "";
  const port = +($(`#${prefix}-pport`)?.value || 0);
  const body = {};
  if (host) body.proxy_host = host;
  if (user) body.proxy_username = user;
  const pass = $(`#${prefix}-ppass`)?.value;
  if (pass) body.proxy_password = pass;
  const key = $(`#${prefix}-pkey`)?.value?.trim();
  if (key) body.proxy_api_key = key;
  if (port) body.proxy_port = port;
  const lab = $(`#${prefix}-plabel`)?.value?.trim();
  if (lab) body.proxy_label = lab;
  body.proxy_provider = "proxyxoay_net";
  return body;
}

async function renderKeys(root) {
  const data = await api("/keys");
  root.innerHTML = `
    <div class="panel">
      <h3>Tạo account / API key</h3>
      <p class="muted">Quản lý tại web admin · gói ký tự (triệu) · max luồng ≤ 5 · gắn proxyxoay riêng</p>
      <div class="grid-2">
        <div class="field"><label>Tên account</label><input id="k-name" value="customer" /></div>
        <div class="field"><label>Max chars / request</label><input id="k-max" type="number" value="950" min="100" max="1000" /></div>
        <div class="field">
          <label>Gói ký tự / ngày</label>
          <select id="k-pkg">
            <option value="1000000">1 triệu (1,000,000)</option>
            <option value="5000000">5 triệu</option>
            <option value="10000000" selected>10 triệu</option>
            <option value="50000000">50 triệu</option>
            <option value="100000000">100 triệu</option>
            <option value="custom">Tuỳ chỉnh…</option>
          </select>
        </div>
        <div class="field"><label>Quota chars (số)</label><input id="k-qc" type="number" value="10000000" min="1000" step="1000000" /></div>
        <div class="field"><label>Quota jobs / day</label><input id="k-qj" type="number" value="500" /></div>
        <div class="field">
          <label>Max luồng đồng thời (1–5)</label>
          <input id="k-mc" type="number" value="3" min="1" max="5" />
        </div>
        <div class="field" style="grid-column:1/-1"><label>Note</label><input id="k-note" placeholder="ghi chú account" /></div>
      </div>
      <h4 style="margin:1rem 0 0.5rem">Proxyxoay gắn account này (optional)</h4>
      <div class="grid-2">
        <div class="field"><label>Label</label><input id="k-plabel" placeholder="EU line 1" /></div>
        <div class="field"><label>API key proxyxoay</label><input id="k-pkey" /></div>
        <div class="field"><label>Username</label><input id="k-puser" /></div>
        <div class="field"><label>Password</label><input id="k-ppass" type="password" /></div>
        <div class="field"><label>Host</label><input id="k-phost" placeholder="vipvn7.proxyxoay.net" /></div>
        <div class="field"><label>Port</label><input id="k-pport" type="number" value="8978" /></div>
      </div>
      <div class="form-actions">
        <button class="primary" id="k-create" type="button">Create key</button>
      </div>
      <div id="k-new" class="hidden" style="margin-top:1rem"></div>
    </div>
    <div class="panel">
      <h3>Accounts / Keys</h3>
      <table>
        <thead>
          <tr>
            <th>Name</th><th>Prefix</th><th>On</th>
            <th>Max/req</th><th>Gói/ngày</th><th>Used</th>
            <th>Jobs</th><th>Luồng</th><th>Proxy</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${(data.keys || [])
            .map((k) => {
              const hasPx = !!(k.proxy_host && k.proxy_username);
              return `
            <tr data-id="${k.id}">
              <td>${esc(k.name)}</td>
              <td class="mono">${esc(k.key_prefix)}</td>
              <td>${k.enabled ? badge("ready") : badge("dead")}</td>
              <td><input class="k-edit" data-f="max_chars" type="number" min="100" max="1000" value="${k.max_chars ?? ""}" style="width:80px" /></td>
              <td>
                <input class="k-edit" data-f="quota_chars_day" type="number" min="1000" step="1000000"
                  value="${k.quota_chars_day ?? ""}" style="width:110px" title="${fmtM(k.quota_chars_day)}" />
                <div class="muted" style="font-size:11px">${fmtM(k.quota_chars_day)}/ngày</div>
              </td>
              <td>${fmtM(k.chars_used_day)} / ${fmtM(k.quota_chars_day)}</td>
              <td>${k.jobs_used_day}/${k.quota_jobs_day}</td>
              <td><input class="k-edit" data-f="max_concurrent" type="number" min="1" max="5" value="${Math.min(5, k.max_concurrent ?? 2)}" style="width:60px" /></td>
              <td class="mono" style="font-size:11px">${hasPx ? esc((k.proxy_host || "") + ":" + (k.proxy_port || "")) : "—"}</td>
              <td class="row" style="margin:0;flex-wrap:wrap;gap:4px">
                <button type="button" data-act="save">Save</button>
                <button type="button" data-act="proxy">Proxy</button>
                <button type="button" data-act="toggle">${k.enabled ? "Disable" : "Enable"}</button>
                <button type="button" class="danger" data-act="del">Delete</button>
              </td>
            </tr>
            <tr class="proxy-row hidden" data-proxy-for="${k.id}">
              <td colspan="10">
                <div class="grid-2" style="padding:0.5rem 0">
                  <div class="field"><label>Proxy label</label><input id="kp-${k.id}-plabel" value="${esc(k.proxy_label || "")}" /></div>
                  <div class="field"><label>Proxyxoay API key</label><input id="kp-${k.id}-pkey" value="${esc(k.proxy_api_key || "")}" /></div>
                  <div class="field"><label>Username</label><input id="kp-${k.id}-puser" value="${esc(k.proxy_username || "")}" /></div>
                  <div class="field"><label>Password</label><input id="kp-${k.id}-ppass" type="password" placeholder="(giữ nguyên nếu trống)" /></div>
                  <div class="field"><label>Host</label><input id="kp-${k.id}-phost" value="${esc(k.proxy_host || "")}" /></div>
                  <div class="field"><label>Port</label><input id="kp-${k.id}-pport" type="number" value="${k.proxy_port || 8978}" /></div>
                </div>
                <button type="button" class="primary" data-act="save-proxy" data-id="${k.id}">Lưu proxy account</button>
              </td>
            </tr>`;
            })
            .join("")}
        </tbody>
      </table>
    </div>
  `;

  const pkg = $("#k-pkg");
  if (pkg) {
    pkg.onchange = () => {
      if (pkg.value !== "custom") $("#k-qc").value = pkg.value;
    };
  }

  $("#k-create").onclick = async () => {
    try {
      let mc = +($("#k-mc").value || 3);
      if (mc < 1) mc = 1;
      if (mc > 5) mc = 5;
      const body = {
        name: $("#k-name").value || "customer",
        note: $("#k-note").value || "",
        max_chars: +($("#k-max").value || 950),
        quota_chars_day: +($("#k-qc").value || 10000000),
        quota_jobs_day: +($("#k-qj").value || 500),
        max_concurrent: mc,
        ...readProxyFields("k"),
      };
      const res = await api("/keys", { method: "POST", body: JSON.stringify(body) });
      const box = $("#k-new");
      box.classList.remove("hidden");
      box.style.display = "block";
      box.innerHTML = `<p class="muted">${esc(res.note)}</p><pre class="keybox">${esc(res.key)}</pre>
        <p class="muted">Gói ${fmtM(body.quota_chars_day)}/ngày · max ${mc} luồng · proxy ${res.has_proxy ? "OK" : "không"}</p>
        <button type="button" id="k-copy">Copy key</button>`;
      $("#k-copy").onclick = () => {
        navigator.clipboard.writeText(res.key);
        toast("Copied");
      };
      toast("API key created");
      setTimeout(() => navigate("keys"), 800);
    } catch (e) {
      toast(e.message);
    }
  };

  root.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    const saveBtn = tr.querySelector('[data-act="save"]');
    if (saveBtn)
      saveBtn.onclick = async () => {
        const body = {};
        tr.querySelectorAll(".k-edit").forEach((inp) => {
          if (inp.value === "") return;
          let v = +inp.value;
          if (inp.dataset.f === "max_concurrent") {
            if (v < 1) v = 1;
            if (v > 5) v = 5;
          }
          body[inp.dataset.f] = v;
        });
        try {
          await api(`/keys/${id}`, { method: "PATCH", body: JSON.stringify(body) });
          toast("Saved");
          navigate("keys");
        } catch (e) {
          toast(e.message);
        }
      };
    const pxBtn = tr.querySelector('[data-act="proxy"]');
    if (pxBtn)
      pxBtn.onclick = () => {
        const row = root.querySelector(`tr[data-proxy-for="${id}"]`);
        if (row) row.classList.toggle("hidden");
      };
    const tog = tr.querySelector('[data-act="toggle"]');
    if (tog)
      tog.onclick = async () => {
        const en = tog.textContent === "Enable";
        try {
          await api(`/keys/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: en }) });
          navigate("keys");
        } catch (e) {
          toast(e.message);
        }
      };
    const del = tr.querySelector('[data-act="del"]');
    if (del)
      del.onclick = async () => {
        if (!confirm("Delete this key?")) return;
        try {
          await api(`/keys/${id}`, { method: "DELETE" });
          navigate("keys");
        } catch (e) {
          toast(e.message);
        }
      };
  });

  root.querySelectorAll('[data-act="save-proxy"]').forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      const body = {
        proxy_label: $(`#kp-${id}-plabel`)?.value || "",
        proxy_api_key: $(`#kp-${id}-pkey`)?.value || "",
        proxy_username: $(`#kp-${id}-puser`)?.value || "",
        proxy_host: $(`#kp-${id}-phost`)?.value || "",
        proxy_port: +($(`#kp-${id}-pport`)?.value || 0),
        proxy_provider: "proxyxoay_net",
      };
      const pw = $(`#kp-${id}-ppass`)?.value;
      if (pw) body.proxy_password = pw;
      try {
        await api(`/keys/${id}`, { method: "PATCH", body: JSON.stringify(body) });
        // also upsert pool slot for this account
        if (body.proxy_host && body.proxy_username) {
          try {
            await api("/proxies", {
              method: "POST",
              body: JSON.stringify({
                id: `key${id}`,
                label: body.proxy_label || `account-${id}`,
                enabled: true,
                provider: "proxyxoay_net",
                api_key: body.proxy_api_key,
                username: body.proxy_username,
                password: body.proxy_password || "",
                host: body.proxy_host,
                port: body.proxy_port || 8978,
              }),
            });
          } catch (_) {}
        }
        toast("Proxy account saved");
        navigate("keys");
      } catch (e) {
        toast(e.message);
      }
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
      <button class="primary" id="p-save" type="button">Save proxy</button>
    </div>
    <div class="panel">
      <h3>Pool (${data.stats.ready}/${data.stats.total} ready)</h3>
      <table>
        <thead><tr><th>ID</th><th>Label</th><th>State</th><th>Exit</th><th>In flight</th><th>OK/IP</th><th>Error</th><th></th></tr></thead>
        <tbody>
          ${(data.proxies || [])
            .map(
              (p) => `
            <tr>
              <td class="mono">${esc(p.id)}</td>
              <td>${esc(p.label)}</td>
              <td>${badge(p.state)}</td>
              <td class="mono">${esc(p.exit_ip || "—")}</td>
              <td>${p.in_flight}</td>
              <td>${p.ok_on_ip}</td>
              <td class="muted" style="max-width:180px">${esc(p.last_error || "")}</td>
              <td class="row" style="margin:0">
                <button type="button" data-rot="${esc(p.id)}">Rotate IP</button>
                <button type="button" class="danger" data-del="${esc(p.id)}">Delete</button>
              </td>
            </tr>`
            )
            .join("") || `<tr><td colspan="8" class="muted">Empty — add proxies above</td></tr>`}
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
    } catch (e) {
      toast(e.message);
    }
  };
  root.querySelectorAll("[data-rot]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      try {
        await api(`/proxies/${b.dataset.rot}/rotate`, { method: "POST" });
        toast("Rotated");
        navigate("proxies");
      } catch (e) {
        toast(e.message);
        b.disabled = false;
      }
    };
  });
  root.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("Delete proxy?")) return;
      try {
        await api(`/proxies/${b.dataset.del}`, { method: "DELETE" });
        navigate("proxies");
      } catch (e) {
        toast(e.message);
      }
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
        <strong>Default max chars</strong> only applies to <em>new</em> API keys.
        Tick the box below to overwrite all existing keys.
      </p>
      <div class="grid-2">
        ${allFields
          .map(
            ([k, label, type]) => `
          <div class="field">
            <label>${esc(label)}</label>
            <input id="s-${k}" type="${type}" value="${esc(s[k] ?? "")}" ${type === "number" ? 'min="1" step="1"' : ""} />
          </div>`
          )
          .join("")}
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
          ${(data.jobs || [])
            .map(
              (j) => `
            <tr>
              <td class="mono">${esc(j.id).slice(0, 18)}</td>
              <td>${badge(j.status)}</td>
              <td>${j.text_chars}</td>
              <td>${esc(j.proxy_id || "—")}</td>
              <td class="mono">${esc(j.exit_ip || "—")}</td>
              <td>${j.duration_ms ?? "—"}</td>
              <td class="muted" style="max-width:160px">${esc((j.error || "").slice(0, 80))}</td>
              <td>${esc(j.text_preview || "")}</td>
            </tr>`
            )
            .join("")}
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
          ${(u.by_day || [])
            .map(
              (d) => `
            <tr><td>${esc(d.day)}</td><td>${d.chars}</td><td>${d.ok_jobs}</td><td>${d.events}</td></tr>
          `
            )
            .join("") || `<tr><td colspan="4" class="muted">No usage yet</td></tr>`}
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
function wireUi() {
  const form = $("#login-form");
  if (form) form.addEventListener("submit", login);
  const lo = $("#btn-logout");
  if (lo) lo.addEventListener("click", logout);
  $$(".nav-btn[data-page]").forEach((b) =>
    b.addEventListener("click", () => navigate(b.dataset.page))
  );
  const btnRefresh = $("#btn-refresh");
  if (btnRefresh) btnRefresh.addEventListener("click", () => navigate(state.page || "overview"));
}

wireUi();

(async () => {
  showLogin();
  if (!state.token) return;
  try {
    await api("/dashboard");
    showApp();
    await navigate("overview");
  } catch {
    state.token = "";
    localStorage.removeItem("tts_admin_token");
    showLogin();
  }
})();
