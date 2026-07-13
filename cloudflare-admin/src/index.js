/**
 * Cloudflare Worker + D1 — Web Admin API
 * Local tool (preview_studio) does NOT use this.
 *
 * Routes:
 *   POST /api/login
 *   POST /api/logout
 *   GET  /api/dashboard
 *   CRUD /api/accounts
 *   CRUD /api/proxies
 *   CRUD /api/packages
 *   Static admin UI via assets binding
 */

const COOKIE = "tts_cf_admin";
const COOKIE_TTL = 60 * 60 * 24 * 7;

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

async function handleApi(req, env) {
  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/api/, "") || "/";
  const method = req.method.toUpperCase();

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

  // all below need admin
  const session = await requireAdmin(env, req);
  if (!session) return err("admin auth required", 401);

  // ── dashboard ──
  if (path === "/dashboard" && method === "GET") {
    const accounts = await env.DB.prepare(
      "SELECT COUNT(*) AS c FROM accounts"
    ).first();
    const proxies = await env.DB.prepare(
      "SELECT COUNT(*) AS c FROM proxies WHERE enabled = 1"
    ).first();
    const packages = await env.DB.prepare(
      "SELECT id, name, chars FROM packages ORDER BY chars ASC"
    ).all();
    return json({
      accounts: accounts?.c || 0,
      proxies_ready: proxies?.c || 0,
      packages: packages.results || [],
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
      .bind(id, b.name || id, Number(b.chars) || 1000000, b.note || "")
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
    const rows = (r.results || []).map((p) => ({
      ...p,
      password: undefined,
      has_password: true,
    }));
    return json({ proxies: rows });
  }
  if (path === "/proxies" && method === "POST") {
    const b = await req.json();
    const id = b.id || "px_" + crypto.randomUUID().slice(0, 8);
    const existing = await env.DB.prepare(
      "SELECT password FROM proxies WHERE id = ?"
    )
      .bind(id)
      .first();
    const password =
      b.password || (existing && existing.password) || "";
    await env.DB.prepare(
      `INSERT INTO proxies (id, label, enabled, provider, api_key, username, password, host, port, note, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
       ON CONFLICT(id) DO UPDATE SET
         label=excluded.label, enabled=excluded.enabled, provider=excluded.provider,
         api_key=excluded.api_key, username=excluded.username,
         password=CASE WHEN excluded.password = '' THEN proxies.password ELSE excluded.password END,
         host=excluded.host, port=excluded.port, note=excluded.note`
    )
      .bind(
        id,
        b.label || id,
        b.enabled === false ? 0 : 1,
        b.provider || "proxyxoay_net",
        b.api_key || "",
        b.username || "",
        password,
        b.host || "",
        Number(b.port) || 0,
        b.note || ""
      )
      .run();
    return json({ ok: true, id });
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
              char_quota, chars_used, max_workers, proxy_id, proxy_host, proxy_port,
              proxy_username, proxy_label, api_key_prefix, created_at, last_login_at
       FROM accounts ORDER BY created_at DESC`
    ).all();
    const rows = (r.results || []).map((a) => ({
      ...a,
      chars_left: Math.max(0, (a.char_quota || 0) - (a.chars_used || 0)),
      has_proxy: !!(a.proxy_host && a.proxy_username) || !!a.proxy_id,
      max_workers: Math.min(5, Math.max(1, a.max_workers || 1)),
    }));
    return json({ accounts: rows });
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

    await env.DB.prepare(
      `INSERT INTO accounts (
        id, username, password_salt, password_hash, role, enabled, note,
        package_id, package_name, char_quota, chars_used, max_workers,
        proxy_id, proxy_provider, proxy_api_key, proxy_username, proxy_password,
        proxy_host, proxy_port, proxy_label, api_key_hash, api_key_prefix, created_at
      ) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))`
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
        b.proxy_id || "",
        b.proxy_provider || "proxyxoay_net",
        b.proxy_api_key || "",
        b.proxy_username || "",
        b.proxy_password || "",
        b.proxy_host || "",
        Number(b.proxy_port) || 0,
        b.proxy_label || "",
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

    await env.DB.prepare(
      `UPDATE accounts SET
        role=?, enabled=?, note=?,
        package_id=?, package_name=?, char_quota=?,
        max_workers=?,
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
        b.proxy_id != null ? b.proxy_id : row.proxy_id,
        b.proxy_provider || row.proxy_provider,
        b.proxy_api_key != null ? b.proxy_api_key : row.proxy_api_key,
        b.proxy_username != null ? b.proxy_username : row.proxy_username,
        b.proxy_password || "",
        b.proxy_password || "",
        b.proxy_host != null ? b.proxy_host : row.proxy_host,
        b.proxy_port != null ? Number(b.proxy_port) : row.proxy_port,
        b.proxy_label != null ? b.proxy_label : row.proxy_label,
        password_salt,
        password_hash,
        b.chars_used != null ? 1 : null,
        b.chars_used != null ? Number(b.chars_used) : 0,
        id
      )
      .run();
    return json({ ok: true, id });
  }

  if (path.startsWith("/accounts/") && method === "DELETE") {
    const id = path.split("/")[2];
    await env.DB.prepare("DELETE FROM accounts WHERE id = ?").bind(id).run();
    return json({ ok: true });
  }

  return err("not found", 404);
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);

    // API
    if (url.pathname.startsWith("/api/")) {
      try {
        return await handleApi(req, env);
      } catch (e) {
        return err(String(e.message || e), 500);
      }
    }

    // Static admin UI
    if (env.ASSETS) {
      return env.ASSETS.fetch(req);
    }
    return new Response("tts-admin-web OK — set assets + D1", { status: 200 });
  },
};
