/**
 * Cloudflare Worker + D1 — Web Admin API
 * Local tool (preview_studio) does NOT use this.
 *
 * Public (custom domain):
 *   https://tts-origin.liveyt.pro/admin/
 *   https://tts-origin.liveyt.pro/admin/api/*
 *
 * Also works on workers.dev root:
 *   /  and  /api/*
 *
 * Routes:
 *   POST /api/login  (or /admin/api/login)
 *   POST /api/logout
 *   POST /api/user/login
 *   POST /api/user/presence   (studio: start|heartbeat|stop gen)
 *   GET  /api/online         (admin: ai đang gen)
 *   GET  /api/dashboard
 *   CRUD /api/accounts
 *   CRUD /api/proxies
 *   CRUD /api/packages
 *   Static admin UI via assets binding
 */

const COOKIE = "tts_cf_admin";
const COOKIE_TTL = 60 * 60 * 24 * 7;
/** Seconds without heartbeat → no longer "online gen" */
const GEN_ONLINE_TTL = 120;
const PRESENCE_TOKEN_TTL = 60 * 60 * 24 * 30;

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...headers,
    },
  });
}

function err(msg, status = 400) {
  return json({ detail: msg }, status);
}

async function sha256(text) {
  const data = new TextEncoder().encode(text);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Bytes(bytes) {
  const buf = await crypto.subtle.digest("SHA-256", bytes);
  return new Uint8Array(buf);
}

/** Default seal key — set env PROXY_SEAL_KEY in production */
function sealKeyFromEnv(env) {
  return String(env.PROXY_SEAL_KEY || env.API_SECRET || "huytts2026").trim();
}

function b64encode(u8) {
  let s = "";
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  return btoa(s);
}

function b64decode(str) {
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Keystream XOR + HMAC-SHA256 tag (tool-compatible, no external deps) */
async function sealJson(obj, passphrase) {
  const plain = new TextEncoder().encode(JSON.stringify(obj));
  const key = await sha256Bytes(new TextEncoder().encode(passphrase));
  const nonce = crypto.getRandomValues(new Uint8Array(16));
  const stream = new Uint8Array(plain.length);
  let offset = 0;
  let i = 0;
  while (offset < plain.length) {
    const ctr = new Uint8Array(4);
    ctr[0] = (i >>> 24) & 255;
    ctr[1] = (i >>> 16) & 255;
    ctr[2] = (i >>> 8) & 255;
    ctr[3] = i & 255;
    const block = await sha256Bytes(
      (() => {
        const o = new Uint8Array(key.length + nonce.length + 4);
        o.set(key, 0);
        o.set(nonce, key.length);
        o.set(ctr, key.length + nonce.length);
        return o;
      })()
    );
    const n = Math.min(32, plain.length - offset);
    stream.set(block.subarray(0, n), offset);
    offset += n;
    i += 1;
  }
  const ct = new Uint8Array(plain.length);
  for (let j = 0; j < plain.length; j++) ct[j] = plain[j] ^ stream[j];
  // HMAC-SHA256(key, nonce||ct) first 16 bytes as tag
  const macKey = await crypto.subtle.importKey(
    "raw",
    key,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const macData = new Uint8Array(nonce.length + ct.length);
  macData.set(nonce, 0);
  macData.set(ct, nonce.length);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", macKey, macData));
  const tag = sig.subarray(0, 16);
  const out = new Uint8Array(16 + 16 + ct.length);
  out.set(nonce, 0);
  out.set(tag, 16);
  out.set(ct, 32);
  return b64encode(out);
}

function parseShopNote(note) {
  // SHOP|nhamang=random|tinhthanh=0|whitelist=
  const o = { shop_nhamang: "random", shop_tinhthanh: 0, shop_whitelist: "", shop_method: "GET" };
  const n = String(note || "");
  if (!n.startsWith("SHOP|")) return o;
  n.split("|").slice(1).forEach((part) => {
    const eq = part.indexOf("=");
    if (eq < 0) return;
    const k = part.slice(0, eq).trim();
    const v = part.slice(eq + 1).trim();
    if (k === "nhamang") o.shop_nhamang = v || "random";
    if (k === "tinhthanh") o.shop_tinhthanh = v === "" ? 0 : Number(v) || 0;
    if (k === "whitelist") o.shop_whitelist = v;
    if (k === "method") o.shop_method = (v || "GET").toUpperCase();
  });
  return o;
}

function buildShopNote(b) {
  const nh = b.shop_nhamang || b.nhamang || "random";
  const tt = b.shop_tinhthanh != null ? b.shop_tinhthanh : b.tinhthanh != null ? b.tinhthanh : 0;
  const wl = b.shop_whitelist || b.whitelist || "";
  const m = (b.shop_method || b.method || "GET").toUpperCase();
  return `SHOP|nhamang=${nh}|tinhthanh=${tt}|whitelist=${wl}|method=${m}`;
}

function hasProxyCreds({ host, username, api_key, provider }) {
  const p = String(provider || "").toLowerCase();
  if (p.includes("shop")) return !!String(api_key || "").trim();
  if (String(api_key || "").trim()) return true;
  return !!(host && username);
}

async function buildProxyPayload(row, poolRow) {
  const provider =
    (poolRow && poolRow.provider) ||
    row.proxy_provider ||
    "proxyxoay_net";
  const api_key =
    row.proxy_api_key || (poolRow && poolRow.api_key) || "";
  const host = row.proxy_host || (poolRow && poolRow.host) || "";
  const port = Number(row.proxy_port) || (poolRow && Number(poolRow.port)) || 0;
  const username =
    row.proxy_username || (poolRow && poolRow.username) || "";
  const password =
    row.proxy_password || (poolRow && poolRow.password) || "";
  const label =
    row.proxy_label || (poolRow && poolRow.label) || "";
  const note = (poolRow && poolRow.note) || row.note || "";
  const shop = parseShopNote(note);
  return {
    id: row.proxy_id || (poolRow && poolRow.id) || "",
    label,
    provider,
    api_key,
    username,
    password,
    host,
    port,
    ...shop,
  };
}

function parseCookies(req) {
  const h = req.headers.get("Cookie") || "";
  const out = {};
  h.split(";").forEach((p) => {
    const [k, ...r] = p.trim().split("=");
    if (k) out[k] = decodeURIComponent(r.join("=") || "");
  });
  return out;
}

function getToken(req) {
  const auth = req.headers.get("Authorization") || "";
  if (auth.toLowerCase().startsWith("bearer ")) return auth.slice(7).trim();
  const x = req.headers.get("X-Admin-Token");
  if (x) return x.trim();
  return parseCookies(req)[COOKIE] || "";
}

async function requireAdmin(env, req) {
  const token = getToken(req);
  if (!token) return null;
  const row = await env.DB.prepare(
    "SELECT expires_at FROM admin_sessions WHERE token = ?"
  )
    .bind(token)
    .first();
  if (!row || row.expires_at < Date.now() / 1000) return null;
  return token;
}

function setCookie(token) {
  const secure = "; Secure";
  return `${COOKIE}=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${COOKIE_TTL}${secure}`;
}

function clearCookie() {
  return `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`;
}

/** Strip /admin and/or /api prefixes → path used by handlers ("/login", "/accounts", …) */
function apiPathFromUrl(pathname) {
  let p = pathname || "/";
  if (p.startsWith("/admin/api")) p = p.slice("/admin/api".length) || "/";
  else if (p === "/admin/api") p = "/";
  else if (p.startsWith("/api")) p = p.slice("/api".length) || "/";
  if (!p.startsWith("/")) p = "/" + p;
  return p;
}

let _presenceSchemaReady = false;

async function ensurePresenceSchema(env) {
  if (!env.DB) return;
  if (_presenceSchemaReady) return;
  try {
    await env.DB.batch([
      env.DB.prepare(
        `CREATE TABLE IF NOT EXISTS presence_tokens (
          token TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          username TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          expires_at REAL NOT NULL
        )`
      ),
      env.DB.prepare(
        `CREATE INDEX IF NOT EXISTS idx_presence_tokens_acc ON presence_tokens(account_id)`
      ),
      env.DB.prepare(
        `CREATE TABLE IF NOT EXISTS gen_online (
          account_id TEXT PRIMARY KEY,
          username TEXT NOT NULL,
          session_id TEXT DEFAULT '',
          kind TEXT DEFAULT 'preview',
          workers INTEGER DEFAULT 1,
          ok_chunks INTEGER DEFAULT 0,
          fail_chunks INTEGER DEFAULT 0,
          total_chunks INTEGER DEFAULT 0,
          status TEXT DEFAULT 'generating',
          label TEXT DEFAULT '',
          started_at TEXT,
          last_seen REAL NOT NULL,
          client TEXT DEFAULT ''
        )`
      ),
      env.DB.prepare(
        `CREATE INDEX IF NOT EXISTS idx_gen_online_seen ON gen_online(last_seen)`
      ),
      env.DB.prepare(
        `CREATE INDEX IF NOT EXISTS idx_gen_online_status ON gen_online(status)`
      ),
    ]);
    _presenceSchemaReady = true;
  } catch (e) {
    // table may already exist partially — keep serving
    console.log("ensurePresenceSchema", String(e && e.message ? e.message : e));
  }
}

async function countOnlineGen(env) {
  const cutoff = Date.now() / 1000 - GEN_ONLINE_TTL;
  try {
    const row = await env.DB.prepare(
      `SELECT COUNT(*) AS c FROM gen_online
       WHERE status = 'generating' AND last_seen >= ?`
    )
      .bind(cutoff)
      .first();
    return Number(row?.c || 0);
  } catch {
    return 0;
  }
}

async function handleApi(req, env) {
  const url = new URL(req.url);
  const path = apiPathFromUrl(url.pathname);
  const method = req.method.toUpperCase();

  await ensurePresenceSchema(env);

  // ── login (no auth) ──
  if (path === "/login" && method === "POST") {
    const body = await req.json().catch(() => ({}));
    const got = String(body.password || "").trim();
    const expected = String(env.ADMIN_PASSWORD || "30102002").trim();
    if (!got || got !== expected) return err("wrong password", 401);
    const token = crypto.randomUUID() + crypto.randomUUID().replace(/-/g, "");
    const exp = Date.now() / 1000 + COOKIE_TTL;
    await env.DB.prepare(
      "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, datetime('now'), ?)"
    )
      .bind(token, exp)
      .run();
    return json(
      { ok: true, token },
      200,
      { "Set-Cookie": setCookie(token) }
    );
  }

  if (path === "/logout" && method === "POST") {
    const token = getToken(req);
    if (token) {
      await env.DB.prepare("DELETE FROM admin_sessions WHERE token = ?")
        .bind(token)
        .run();
    }
    return json({ ok: true }, 200, { "Set-Cookie": clearCookie() });
  }

  // ── User login for Preview Studio tool (not admin password) ──
  // POST { username, password } → account profile for local tool
  if (
    (path === "/user/login" || path === "/auth/login") &&
    method === "POST"
  ) {
    const body = await req.json().catch(() => ({}));
    const username = String(body.username || "").trim();
    const password = String(body.password || "").trim();
    if (!username || !password) return err("username/password required", 400);

    const row = await env.DB.prepare(
      "SELECT * FROM accounts WHERE username = ?"
    )
      .bind(username)
      .first();
    if (!row) return err("wrong username or password", 401);
    if (!row.enabled) return err("account disabled", 403);

    const expected = await sha256(`${row.password_salt}:${password}`);
    if (expected !== row.password_hash) {
      return err("wrong username or password", 401);
    }

    // resolve proxies from account_proxies (many-to-many)
    const proxiesResult = await env.DB.prepare(
      `SELECT ap.proxy_id, ap.priority, ap.enabled,
              p.label, p.provider, p.host, p.port, p.username, p.password, p.api_key
       FROM account_proxies ap
       LEFT JOIN proxies p ON ap.proxy_id = p.id
       WHERE ap.account_id = ? AND ap.enabled = 1
       ORDER BY ap.priority ASC`
    ).bind(row.id).all();
    
    const proxiesList = (proxiesResult.results || []).map(p => ({
      id: p.proxy_id,
      label: p.label || "",
      provider: p.provider || "proxyxoay_net",
      host: p.host || "",
      port: Number(p.port) || 0,
      username: p.username || "",
      password: p.password || "",
      api_key: p.api_key || "",
      priority: p.priority
    }));

    await env.DB.prepare(
      "UPDATE accounts SET last_login_at = datetime('now') WHERE id = ?"
    )
      .bind(row.id)
      .run();

    // presence token for studio gen online heartbeats
    const presence_token =
      crypto.randomUUID().replace(/-/g, "") +
      crypto.randomUUID().replace(/-/g, "").slice(0, 16);
    const presence_exp = Date.now() / 1000 + PRESENCE_TOKEN_TTL;
    try {
      await env.DB.prepare("DELETE FROM presence_tokens WHERE account_id = ?")
        .bind(row.id)
        .run();
      await env.DB.prepare(
        `INSERT INTO presence_tokens (token, account_id, username, created_at, expires_at)
         VALUES (?, ?, ?, datetime('now'), ?)`
      )
        .bind(presence_token, row.id, row.username, presence_exp)
        .run();
    } catch (e) {
      console.log("presence_token insert", String(e && e.message ? e.message : e));
    }

    // Encrypt all proxies for tool; do not send plain secrets over the wire
    let proxies_sealed = "";
    if (proxiesList.length > 0) {
      try {
        proxies_sealed = await sealJson({ proxies: proxiesList }, sealKeyFromEnv(env));
      } catch (e) {
        return err("proxies seal failed: " + String(e.message || e), 500);
      }
    }

    return json({
      ok: true,
      source: "cloudflare-d1",
      seal_version: 1,
      presence_token,
      account: {
        id: row.id,
        username: row.username,
        role: row.role || "user",
        enabled: !!row.enabled,
        note: row.note || "",
        package_id: row.package_id || "",
        package_name: row.package_name || "",
        char_quota: Number(row.char_quota),
        chars_used: Number(row.chars_used) || 0,
        unlimited:
          Number(row.char_quota) <= 0 || Number(row.char_quota) === -1,
        chars_left:
          Number(row.char_quota) <= 0 || Number(row.char_quota) === -1
            ? -1
            : Math.max(
                0,
                (Number(row.char_quota) || 0) - (Number(row.chars_used) || 0)
              ),
        max_workers: Math.min(5, Math.max(1, Number(row.max_workers) || 1)),
        max_chars: Number(row.max_chars) || 0,
        has_proxy: proxiesList.length > 0,
        proxies_sealed,
        proxies_count: proxiesList.length,
        presence_token,
        // password material so local tool can cache offline login
        password_salt: row.password_salt,
        password_hash: row.password_hash,
      },
    });
  }

  // ── Studio: gen presence (online = đang gen TTS) ──
  // POST /user/presence  { token|presence_token, action, kind, workers, ok, fail, total, label, session_id }
  if (
    (path === "/user/presence" ||
      path === "/presence/heartbeat" ||
      path === "/gen/presence") &&
    method === "POST"
  ) {
    const b = await req.json().catch(() => ({}));
    const token = String(
      b.token || b.presence_token || req.headers.get("X-Presence-Token") || ""
    ).trim();
    if (!token) return err("presence token required", 401);

    const tok = await env.DB.prepare(
      "SELECT * FROM presence_tokens WHERE token = ?"
    )
      .bind(token)
      .first();
    if (!tok) return err("invalid presence token", 401);
    if (Number(tok.expires_at) < Date.now() / 1000) {
      await env.DB.prepare("DELETE FROM presence_tokens WHERE token = ?")
        .bind(token)
        .run();
      return err("presence token expired — login again", 401);
    }

    const account = await env.DB.prepare(
      "SELECT id, username, enabled FROM accounts WHERE id = ?"
    )
      .bind(tok.account_id)
      .first();
    if (!account || !account.enabled) return err("account disabled", 403);

    const action = String(b.action || "heartbeat").toLowerCase();
    const now = Date.now() / 1000;
    const kind = String(b.kind || "preview").slice(0, 32);
    const workers = Math.min(5, Math.max(0, Number(b.workers) || 0));
    const ok_chunks = Math.max(0, Number(b.ok != null ? b.ok : b.ok_chunks) || 0);
    const fail_chunks = Math.max(
      0,
      Number(b.fail != null ? b.fail : b.fail_chunks) || 0
    );
    const total_chunks = Math.max(
      0,
      Number(b.total != null ? b.total : b.total_chunks) || 0
    );
    const label = String(b.label || "").slice(0, 120);
    const session_id = String(b.session_id || "").slice(0, 64);
    const client = String(b.client || "preview_studio").slice(0, 64);

    if (action === "stop" || action === "end" || action === "idle") {
      await env.DB.prepare(
        `UPDATE gen_online SET
           status='idle', last_seen=?, ok_chunks=?, fail_chunks=?, total_chunks=?
         WHERE account_id=?`
      )
        .bind(now, ok_chunks, fail_chunks, total_chunks, account.id)
        .run();
      return json({ ok: true, status: "idle", online: false });
    }

    // start | heartbeat | generating
    const existing = await env.DB.prepare(
      "SELECT started_at, session_id FROM gen_online WHERE account_id = ?"
    )
      .bind(account.id)
      .first();
    const isStart = action === "start" || action === "begin";
    const started_at =
      isStart || !existing?.started_at
        ? new Date().toISOString()
        : existing.started_at;
    const sid =
      session_id ||
      (isStart ? crypto.randomUUID().replace(/-/g, "").slice(0, 16) : "") ||
      existing?.session_id ||
      "";

    await env.DB.prepare(
      `INSERT INTO gen_online (
         account_id, username, session_id, kind, workers,
         ok_chunks, fail_chunks, total_chunks, status, label,
         started_at, last_seen, client
       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(account_id) DO UPDATE SET
         username=excluded.username,
         session_id=CASE WHEN excluded.session_id != '' THEN excluded.session_id ELSE gen_online.session_id END,
         kind=excluded.kind,
         workers=excluded.workers,
         ok_chunks=excluded.ok_chunks,
         fail_chunks=excluded.fail_chunks,
         total_chunks=excluded.total_chunks,
         status='generating',
         label=CASE WHEN excluded.label != '' THEN excluded.label ELSE gen_online.label END,
         started_at=excluded.started_at,
         last_seen=excluded.last_seen,
         client=excluded.client`
    )
      .bind(
        account.id,
        account.username,
        sid,
        kind,
        workers,
        ok_chunks,
        fail_chunks,
        total_chunks,
        "generating",
        label,
        started_at,
        now,
        client
      )
      .run();

    return json({
      ok: true,
      status: "generating",
      online: true,
      account_id: account.id,
      username: account.username,
      ttl: GEN_ONLINE_TTL,
    });
  }

  // all below need admin
  const session = await requireAdmin(env, req);
  if (!session) return err("admin auth required", 401);

  // ── online gen observers ──
  if ((path === "/online" || path === "/presence") && method === "GET") {
    const cutoff = Date.now() / 1000 - GEN_ONLINE_TTL;
    // expire stale rows to idle (best-effort)
    try {
      await env.DB.prepare(
        `UPDATE gen_online SET status='idle'
         WHERE status='generating' AND last_seen < ?`
      )
        .bind(cutoff)
        .run();
    } catch (_) {}

    const r = await env.DB.prepare(
      `SELECT account_id, username, session_id, kind, workers,
              ok_chunks, fail_chunks, total_chunks, status, label,
              started_at, last_seen, client
       FROM gen_online
       WHERE status = 'generating' AND last_seen >= ?
       ORDER BY last_seen DESC
       LIMIT 200`
    )
      .bind(cutoff)
      .all();

    const now = Date.now() / 1000;
    const online = (r.results || []).map((row) => {
      const total = Number(row.total_chunks) || 0;
      const ok = Number(row.ok_chunks) || 0;
      const fail = Number(row.fail_chunks) || 0;
      const done = ok + fail;
      const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
      const age = Math.max(0, Math.round(now - Number(row.last_seen || 0)));
      let duration_s = null;
      if (row.started_at) {
        const t0 = Date.parse(row.started_at);
        if (!Number.isNaN(t0)) duration_s = Math.max(0, Math.round(now * 1000 - t0) / 1000);
      }
      return {
        account_id: row.account_id,
        username: row.username,
        session_id: row.session_id || "",
        kind: row.kind || "preview",
        workers: Number(row.workers) || 0,
        ok_chunks: ok,
        fail_chunks: fail,
        total_chunks: total,
        progress_pct: pct,
        label: row.label || "",
        started_at: row.started_at || "",
        last_seen: Number(row.last_seen) || 0,
        last_seen_ago_s: age,
        duration_s: duration_s != null ? Math.round(duration_s) : null,
        client: row.client || "",
        status: "generating",
      };
    });

    return json({
      online,
      count: online.length,
      ttl: GEN_ONLINE_TTL,
      now: Date.now() / 1000,
    });
  }

  // ── dashboard ──
  if (path === "/dashboard" && method === "GET") {
    const accounts = await env.DB.prepare(
      "SELECT COUNT(*) AS c FROM accounts"
    ).first();
    const proxiesAll = await env.DB.prepare(
      "SELECT COUNT(*) AS c FROM proxies"
    ).first();
    const proxies = await env.DB.prepare(
      "SELECT COUNT(*) AS c FROM proxies WHERE enabled = 1"
    ).first();
    const packages = await env.DB.prepare(
      "SELECT id, name, chars FROM packages ORDER BY chars ASC"
    ).all();
    const pxList = await env.DB.prepare(
      "SELECT id, label, enabled, host, port, username FROM proxies ORDER BY created_at DESC LIMIT 50"
    ).all();
    const accCount = accounts?.c || 0;
    const pxReady = proxies?.c || 0;
    const pxTotal = proxiesAll?.c || 0;
    const online_gen = await countOnlineGen(env);
    // Shape compatible with both CF UI and legacy Windows admin JS
    // (old code did d.usage.jobs_by_status without null-check)
    return json({
      accounts: accCount,
      keys_count: accCount,
      proxies_ready: pxReady,
      online_gen,
      online: online_gen,
      packages: packages.results || [],
      usage: { jobs_by_status: {}, by_day: [] },
      proxy: { ready: pxReady, total: pxTotal },
      settings: {},
      proxies: (pxList.results || []).map((p) => ({
        id: p.id,
        label: p.label || p.id,
        state: p.enabled ? "ready" : "disabled",
        exit_ip: null,
        in_flight: 0,
        ok_on_ip: 0,
        total_ok: 0,
        host: p.host,
        port: p.port,
        username: p.username,
        enabled: !!p.enabled,
      })),
      recent_jobs: [],
      note: "Cloudflare D1 admin — independent from local tool & Windows server",
    });
  }

  // ── packages ──
  if (path === "/packages" && method === "GET") {
    const r = await env.DB.prepare(
      "SELECT * FROM packages ORDER BY chars ASC"
    ).all();
    return json({ packages: r.results || [] });
  }
  if (path === "/packages" && method === "POST") {
    const b = await req.json();
    const id = b.id || "pkg_" + crypto.randomUUID().slice(0, 8);
    await env.DB.prepare(
      `INSERT INTO packages (id, name, chars, note, created_at)
       VALUES (?, ?, ?, ?, datetime('now'))
       ON CONFLICT(id) DO UPDATE SET name=excluded.name, chars=excluded.chars, note=excluded.note`
    )
      .bind(
        id,
        b.name || id,
        // -1 = Unlimited; 0 coerced → -1 if flag unlimited
        b.unlimited || Number(b.chars) === -1 || Number(b.chars) === 0
          ? -1
          : Number(b.chars) || 1000000,
        b.note || (b.unlimited ? "Unlimited" : "")
      )
      .run();
    return json({ ok: true, id });
  }
  if (path.startsWith("/packages/") && method === "DELETE") {
    const id = path.split("/")[2];
    await env.DB.prepare("DELETE FROM packages WHERE id = ?").bind(id).run();
    return json({ ok: true });
  }

  // ── proxies ──
  if (path === "/proxies" && method === "GET") {
    const r = await env.DB.prepare(
      "SELECT id, label, enabled, provider, host, port, username, api_key, note, created_at FROM proxies ORDER BY created_at DESC"
    ).all();
    const rows = (r.results || []).map((p) => {
      const shop = parseShopNote(p.note);
      const key = String(p.api_key || "");
      return {
        ...p,
        password: undefined,
        api_key: key ? key.slice(0, 8) + "…" : "",
        has_password: true,
        has_api_key: !!key,
        provider: p.provider || "proxyxoay_net",
        ...shop,
      };
    });
    return json({ proxies: rows });
  }
  if (path === "/proxies" && method === "POST") {
    const b = await req.json();
    const id = b.id || "px_" + crypto.randomUUID().slice(0, 8);
    const existing = await env.DB.prepare(
      "SELECT password, api_key, note FROM proxies WHERE id = ?"
    )
      .bind(id)
      .first();
    const password =
      b.password || (existing && existing.password) || "";
    const provider = b.provider || "proxyxoay_net";
    const api_key =
      b.api_key || (existing && existing.api_key) || "";
    let note = b.note || "";
    if (String(provider).includes("shop")) {
      note = buildShopNote(b);
    } else if (!note && existing && existing.note) {
      note = existing.note;
    }
    const host =
      provider === "proxyxoay_shop"
        ? b.host || ""
        : b.host || "vipvn7.proxyxoay.net";
    const port =
      provider === "proxyxoay_shop"
        ? Number(b.port) || 0
        : Number(b.port) || 8978;
    await env.DB.prepare(
      `INSERT INTO proxies (id, label, enabled, provider, api_key, username, password, host, port, note, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
       ON CONFLICT(id) DO UPDATE SET
         label=excluded.label, enabled=excluded.enabled, provider=excluded.provider,
         api_key=CASE WHEN excluded.api_key = '' THEN proxies.api_key ELSE excluded.api_key END,
         username=excluded.username,
         password=CASE WHEN excluded.password = '' THEN proxies.password ELSE excluded.password END,
         host=excluded.host, port=excluded.port, note=excluded.note`
    )
      .bind(
        id,
        b.label || id,
        b.enabled === false ? 0 : 1,
        provider,
        api_key,
        b.username || "",
        password,
        host,
        port,
        note
      )
      .run();
    return json({ ok: true, id, provider });
  }
  if (path.startsWith("/proxies/") && method === "DELETE") {
    const id = path.split("/")[2];
    await env.DB.prepare("DELETE FROM proxies WHERE id = ?").bind(id).run();
    return json({ ok: true });
  }

  // ── accounts ──
  if (path === "/accounts" && method === "GET") {
    const r = await env.DB.prepare(
      `SELECT id, username, role, enabled, note, package_id, package_name,
              char_quota, chars_used, max_workers, max_chars, proxy_id, proxy_host, proxy_port,
              proxy_username, proxy_label, api_key_prefix, created_at, last_login_at
       FROM accounts ORDER BY created_at DESC`
    ).all();
    
    // Lấy danh sách proxies cho từng account
    const accountsWithProxies = await Promise.all(
      (r.results || []).map(async (a) => {
        // Lấy proxies từ bảng account_proxies
        const proxiesResult = await env.DB.prepare(
          `SELECT ap.proxy_id, ap.priority, ap.enabled,
                  p.label, p.provider, p.host, p.port, p.api_key
           FROM account_proxies ap
           LEFT JOIN proxies p ON ap.proxy_id = p.id
           WHERE ap.account_id = ?
           ORDER BY ap.priority ASC`
        ).bind(a.id).all();
        
        const proxies = (proxiesResult.results || []).map(p => ({
          proxy_id: p.proxy_id,
          priority: p.priority,
          enabled: p.enabled === 1,
          label: p.label || "",
          provider: p.provider || "proxyxoay_net",
          host: p.host || "",
          port: p.port || 0,
          api_key_preview: p.api_key ? p.api_key.slice(0, 8) + "..." : ""
        }));
        
        return {
          ...a,
          unlimited: Number(a.char_quota) <= 0 || Number(a.char_quota) === -1,
          chars_left:
            Number(a.char_quota) <= 0 || Number(a.char_quota) === -1
              ? -1
              : Math.max(0, (a.char_quota || 0) - (a.chars_used || 0)),
          has_proxy: proxies.length > 0 ||
            !!(a.proxy_host && a.proxy_username) ||
            !!a.proxy_id ||
            !!a.proxy_api_key,
          max_workers: Math.min(5, Math.max(1, a.max_workers || 1)),
          max_chars: Number(a.max_chars) || 0,
          proxy_provider: a.proxy_provider || "proxyxoay_net",
          proxies: proxies
        };
      })
    );

    // attach online-gen flags (TTL window)
    const cutoff = Date.now() / 1000 - GEN_ONLINE_TTL;
    let onlineMap = {};
    try {
      const on = await env.DB.prepare(
        `SELECT account_id, kind, workers, ok_chunks, fail_chunks, total_chunks, last_seen
         FROM gen_online WHERE status='generating' AND last_seen >= ?`
      )
        .bind(cutoff)
        .all();
      for (const row of on.results || []) {
        onlineMap[row.account_id] = row;
      }
    } catch (_) {}

    const accountsOut = accountsWithProxies.map((a) => {
      const o = onlineMap[a.id];
      if (!o) return { ...a, online: false, gen_online: null };
      return {
        ...a,
        online: true,
        gen_online: {
          kind: o.kind,
          workers: o.workers,
          ok_chunks: o.ok_chunks,
          fail_chunks: o.fail_chunks,
          total_chunks: o.total_chunks,
          last_seen: o.last_seen,
        },
      };
    });

    return json({ accounts: accountsOut });
  }

  if (path === "/accounts" && method === "POST") {
    const b = await req.json();
    const username = String(b.username || "").trim();
    const password = String(b.password || "").trim();
    if (!username || !password) return err("username/password required");
    const exists = await env.DB.prepare(
      "SELECT id FROM accounts WHERE username = ?"
    )
      .bind(username)
      .first();
    if (exists) return err("username already exists", 409);

    const salt = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
    const password_hash = await sha256(`${salt}:${password}`);
    const id = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
    let char_quota = Number(b.char_quota) || 1000000;
    let package_name = b.package_name || "";
    if (b.package_id) {
      const pkg = await env.DB.prepare(
        "SELECT * FROM packages WHERE id = ?"
      )
        .bind(b.package_id)
        .first();
      if (pkg) {
        char_quota = pkg.chars;
        package_name = pkg.name;
      }
    }
    const max_workers = Math.min(5, Math.max(1, Number(b.max_workers) || 2));
    // optional api key for clients
    let api_key = b.api_key || "";
    let api_key_hash = "";
    let api_key_prefix = "";
    if (!api_key) {
      api_key =
        "tts_" +
        btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(18))))
          .replace(/[+/=]/g, "")
          .slice(0, 24);
    }
    api_key_hash = await sha256(api_key);
    api_key_prefix = api_key.slice(0, 12) + "…";

    // Expand proxy pool slot → account fields (net or shop)
    let proxy_id = b.proxy_id || "";
    let proxy_provider = b.proxy_provider || "proxyxoay_net";
    let proxy_api_key = b.proxy_api_key || "";
    let proxy_username = b.proxy_username || "";
    let proxy_password = b.proxy_password || "";
    let proxy_host = b.proxy_host || "";
    let proxy_port = Number(b.proxy_port) || 0;
    let proxy_label = b.proxy_label || "";
    if (proxy_id) {
      const px = await env.DB.prepare("SELECT * FROM proxies WHERE id = ?")
        .bind(proxy_id)
        .first();
      if (px) {
        proxy_provider = px.provider || proxy_provider;
        proxy_api_key = px.api_key || proxy_api_key;
        proxy_username = px.username || proxy_username;
        proxy_password = px.password || proxy_password;
        proxy_host = px.host || proxy_host;
        proxy_port = Number(px.port) || proxy_port;
        proxy_label = px.label || proxy_label;
      }
    }

    // 24 columns: 22 binds + chars_used=0 + created_at=datetime('now')
    await env.DB.prepare(
      `INSERT INTO accounts (
        id, username, password_salt, password_hash, role, enabled, note,
        package_id, package_name, char_quota, chars_used, max_workers, max_chars,
        proxy_id, proxy_provider, proxy_api_key, proxy_username, proxy_password,
        proxy_host, proxy_port, proxy_label, api_key_hash, api_key_prefix, created_at
      ) VALUES (
        ?,?,?,?,?,?,?,?,?,?,
        0,
        ?,?,?,?,?,?,?,?,?,?,?,?,
        datetime('now')
      )`
    )
      .bind(
        id,
        username,
        salt,
        password_hash,
        b.role === "admin" ? "admin" : "user",
        b.enabled === false ? 0 : 1,
        b.note || "",
        b.package_id || "",
        package_name,
        char_quota,
        max_workers,
        Number(b.max_chars) || 0,
        proxy_id,
        proxy_provider,
        proxy_api_key,
        proxy_username,
        proxy_password,
        proxy_host,
        Number(proxy_port) || 0,
        proxy_label,
        api_key_hash,
        api_key_prefix
      )
      .run();

    return json({
      ok: true,
      id,
      username,
      api_key,
      note: "Save api_key now — not shown again",
      char_quota,
      max_workers,
      max_chars: Number(b.max_chars) || 0,
      proxy_id,
      proxy_provider,
    });
  }

  if (path.startsWith("/accounts/") && method === "PATCH") {
    const id = path.split("/")[2];
    const b = await req.json();
    const row = await env.DB.prepare("SELECT * FROM accounts WHERE id = ?")
      .bind(id)
      .first();
    if (!row) return err("not found", 404);

    let char_quota = row.char_quota;
    let package_id = row.package_id;
    let package_name = row.package_name;
    if (b.package_id) {
      const pkg = await env.DB.prepare(
        "SELECT * FROM packages WHERE id = ?"
      )
        .bind(b.package_id)
        .first();
      if (pkg) {
        package_id = pkg.id;
        package_name = pkg.name;
        char_quota = pkg.chars;
      }
    }
    if (b.char_quota != null) char_quota = Number(b.char_quota);

    let password_salt = row.password_salt;
    let password_hash = row.password_hash;
    if (b.password) {
      password_salt = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
      password_hash = await sha256(`${password_salt}:${b.password}`);
    }

    const max_workers = Math.min(
      5,
      Math.max(1, Number(b.max_workers != null ? b.max_workers : row.max_workers) || 2)
    );

    const max_chars = b.max_chars != null ? Number(b.max_chars) : (Number(row.max_chars) || 0);

    let proxy_id = b.proxy_id != null ? b.proxy_id : row.proxy_id;
    let proxy_provider = b.proxy_provider || row.proxy_provider || "proxyxoay_net";
    let proxy_api_key =
      b.proxy_api_key != null ? b.proxy_api_key : row.proxy_api_key;
    let proxy_username =
      b.proxy_username != null ? b.proxy_username : row.proxy_username;
    let proxy_password = b.proxy_password || "";
    let proxy_host = b.proxy_host != null ? b.proxy_host : row.proxy_host;
    let proxy_port =
      b.proxy_port != null ? Number(b.proxy_port) : row.proxy_port;
    let proxy_label =
      b.proxy_label != null ? b.proxy_label : row.proxy_label;

    // When proxy_id changes (or set), copy full creds from pool (net/shop)
    if (b.proxy_id != null) {
      if (!b.proxy_id) {
        proxy_id = "";
        proxy_provider = "proxyxoay_net";
        proxy_api_key = "";
        proxy_username = "";
        proxy_password = "";
        proxy_host = "";
        proxy_port = 0;
        proxy_label = "";
      } else {
        const px = await env.DB.prepare("SELECT * FROM proxies WHERE id = ?")
          .bind(b.proxy_id)
          .first();
        if (px) {
          proxy_id = px.id;
          proxy_provider = px.provider || "proxyxoay_net";
          proxy_api_key = px.api_key || "";
          proxy_username = px.username || "";
          proxy_password = px.password || "";
          proxy_host = px.host || "";
          proxy_port = Number(px.port) || 0;
          proxy_label = px.label || "";
        }
      }
    }

    await env.DB.prepare(
      `UPDATE accounts SET
        role=?, enabled=?, note=?,
        package_id=?, package_name=?, char_quota=?,
        max_workers=?, max_chars=?,
        proxy_id=?, proxy_provider=?, proxy_api_key=?,
        proxy_username=?,
        proxy_password=CASE WHEN ? = '' THEN proxy_password ELSE ? END,
        proxy_host=?, proxy_port=?, proxy_label=?,
        password_salt=?, password_hash=?,
        chars_used=CASE WHEN ? IS NOT NULL THEN ? ELSE chars_used END
       WHERE id=?`
    )
      .bind(
        b.role === "admin" ? "admin" : b.role === "user" ? "user" : row.role,
        b.enabled === false ? 0 : b.enabled === true ? 1 : row.enabled,
        b.note != null ? b.note : row.note,
        package_id,
        package_name,
        char_quota,
        max_workers,
        max_chars,
        proxy_id || "",
        proxy_provider,
        proxy_api_key || "",
        proxy_username || "",
        proxy_password,
        proxy_password,
        proxy_host || "",
        Number(proxy_port) || 0,
        proxy_label || "",
        password_salt,
        password_hash,
        b.chars_used != null ? 1 : null,
        b.chars_used != null ? Number(b.chars_used) : 0,
        id
      )
      .run();
    return json({
      ok: true,
      id,
      proxy_id,
      proxy_provider,
      max_chars,
      package_id,
      package_name,
      char_quota,
      max_workers,
      enabled: b.enabled === false ? 0 : b.enabled === true ? 1 : row.enabled,
    });
  }

  if (path.startsWith("/accounts/") && method === "DELETE") {
    const id = path.split("/")[2];
    await env.DB.prepare("DELETE FROM accounts WHERE id = ?").bind(id).run();
    return json({ ok: true });
  }

  // ── account_proxies (many-to-many) ──
  // POST /accounts/:id/proxies - thêm proxy vào account
  if (path.startsWith("/accounts/") && path.endsWith("/proxies") && method === "POST") {
    const parts = path.split("/");
    const accountId = parts[2];
    const b = await req.json();
    const proxyId = (b.proxy_id || "").trim();
    if (!proxyId) return err("proxy_id required");
    
    // Kiểm tra account tồn tại
    const acc = await env.DB.prepare("SELECT id FROM accounts WHERE id = ?").bind(accountId).first();
    if (!acc) return err("account not found", 404);
    
    // Kiểm tra proxy tồn tại
    const px = await env.DB.prepare("SELECT id FROM proxies WHERE id = ?").bind(proxyId).first();
    if (!px) return err("proxy not found", 404);
    
    // Kiểm tra đã gắn chưa
    const existing = await env.DB.prepare(
      "SELECT id FROM account_proxies WHERE account_id = ? AND proxy_id = ?"
    ).bind(accountId, proxyId).first();
    if (existing) return err("proxy already attached");
    
    // Lấy priority cao nhất + 1
    const maxPrio = await env.DB.prepare(
      "SELECT MAX(priority) as max_p FROM account_proxies WHERE account_id = ?"
    ).bind(accountId).first();
    const priority = (maxPrio?.max_p || 0) + 1;
    
    const apId = "ap_" + crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO account_proxies (id, account_id, proxy_id, priority, enabled, created_at)
       VALUES (?, ?, ?, ?, 1, datetime('now'))`
    ).bind(apId, accountId, proxyId, priority).run();
    
    return json({ ok: true, id: apId, priority });
  }

  // DELETE /accounts/:id/proxies/:proxy_id - xóa proxy khỏi account
  if (path.startsWith("/accounts/") && path.includes("/proxies/") && method === "DELETE") {
    const parts = path.split("/");
    const accountId = parts[2];
    const proxyId = parts[4];
    
    await env.DB.prepare(
      "DELETE FROM account_proxies WHERE account_id = ? AND proxy_id = ?"
    ).bind(accountId, proxyId).run();
    
    return json({ ok: true });
  }

  // PATCH /accounts/:id/proxies/:proxy_id - cập nhật priority/enabled
  if (path.startsWith("/accounts/") && path.includes("/proxies/") && method === "PATCH") {
    const parts = path.split("/");
    const accountId = parts[2];
    const proxyId = parts[4];
    const b = await req.json();
    
    const updates = [];
    const values = [];
    
    if (b.priority != null) {
      updates.push("priority = ?");
      values.push(Number(b.priority));
    }
    if (b.enabled != null) {
      updates.push("enabled = ?");
      values.push(b.enabled ? 1 : 0);
    }
    
    if (updates.length === 0) return err("no fields to update");
    
    values.push(accountId, proxyId);
    await env.DB.prepare(
      `UPDATE account_proxies SET ${updates.join(", ")} WHERE account_id = ? AND proxy_id = ?`
    ).bind(...values).run();
    
    return json({ ok: true });
  }

  return err("not found", 404);
}

function isApiPath(pathname) {
  return (
    pathname === "/api" ||
    pathname.startsWith("/api/") ||
    pathname === "/admin/api" ||
    pathname.startsWith("/admin/api/")
  );
}

/**
 * Map request path → asset path under public/
 * Custom domain: /admin/ → /admin/index.html, /admin/static/* → /admin/static/*
 * workers.dev:   / → /index.html (root copy) or /admin/*
 */
function assetPathFromUrl(pathname) {
  let p = pathname || "/";
  if (p === "/admin") return "/admin/index.html";
  if (p === "/admin/" || p === "/admin/index.html") return "/admin/index.html";
  // already under /admin/… keep as-is (files live in public/admin/)
  if (p.startsWith("/admin/")) return p;
  // workers.dev root
  if (p === "/" || p === "") return "/index.html";
  return p;
}

async function serveAsset(req, env, assetPath) {
  if (!env.ASSETS) {
    return new Response("tts-admin-web OK — set assets + D1", { status: 200 });
  }
  // Use a clean absolute URL so Assets doesn't inherit original /admin/ rewrite quirks
  const assetUrl = new URL(assetPath, "https://assets.local");
  const url = new URL(req.url);
  assetUrl.search = url.search;
  const res = await env.ASSETS.fetch(new Request(assetUrl.toString(), {
    method: "GET",
    headers: req.headers,
  }));
  // If Assets returns a redirect loop or miss, surface a clear error
  if (res.status >= 300 && res.status < 400) {
    const loc = res.headers.get("Location") || "";
    if (assetPath.endsWith("/index.html") || assetPath.endsWith(".html")) {
      const retry = await env.ASSETS.fetch(
        new Request(new URL(assetPath, "https://assets.local").toString(), { method: "GET" })
      );
      if (retry.ok) return withNoStore(retry, assetPath);
    }
    return new Response(`asset redirect ${res.status} → ${loc} for ${assetPath}`, {
      status: 502,
      headers: { "Content-Type": "text/plain" },
    });
  }
  return withNoStore(res, assetPath);
}

function withNoStore(res, assetPath) {
  const headers = new Headers(res.headers);
  // Admin UI must never serve stale HTML/JS (edit form + create bugs were masked by cache)
  if (
    assetPath.endsWith(".html") ||
    assetPath.endsWith("/") ||
    assetPath.endsWith(".js") ||
    assetPath.endsWith(".css")
  ) {
    headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
    headers.set("Pragma", "no-cache");
  }
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const pathname = url.pathname;

    // Legacy Windows admin JS path — force browsers onto CF D1 UI
    // (cached HTML still requested /static/admin/app.js and crashed on jobs_by_status)
    if (
      pathname === "/static/admin/app.js" ||
      pathname.startsWith("/static/admin/app.js") ||
      pathname === "/static/admin/index.html" ||
      pathname === "/static/admin/" ||
      pathname === "/static/admin"
    ) {
      return new Response(
        `/* redirected to CF D1 admin */\nlocation.replace("/admin/?v=6");\n`,
        {
          status: 200,
          headers: {
            "Content-Type": "text/javascript; charset=utf-8",
            "Cache-Control": "no-store",
          },
        }
      );
    }

    // Canonical: /admin → /admin/
    if (pathname === "/admin") {
      const dest = new URL(url);
      dest.pathname = "/admin/";
      dest.search = "v=6";
      return Response.redirect(dest.toString(), 302);
    }

    // API (/api/* or /admin/api/*)
    if (isApiPath(pathname)) {
      try {
        return await handleApi(req, env);
      } catch (e) {
        return err(String(e.message || e), 500);
      }
    }

    // Static admin UI
    const assetPath = assetPathFromUrl(pathname);
    return serveAsset(req, env, assetPath);
  },
};
