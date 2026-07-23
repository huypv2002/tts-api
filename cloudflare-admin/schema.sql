-- Cloudflare D1 — TTS Admin (web only, not local tool DB)

CREATE TABLE IF NOT EXISTS admin_sessions (
  token TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS packages (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  chars INTEGER NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxies (
  id TEXT PRIMARY KEY,
  label TEXT DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  provider TEXT DEFAULT 'proxyxoay_net',
  api_key TEXT DEFAULT '',
  username TEXT DEFAULT '',
  password TEXT DEFAULT '',
  host TEXT DEFAULT '',
  port INTEGER DEFAULT 0,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  enabled INTEGER NOT NULL DEFAULT 1,
  note TEXT DEFAULT '',
  -- gói ký tự
  package_id TEXT DEFAULT '',
  package_name TEXT DEFAULT '',
  char_quota INTEGER NOT NULL DEFAULT 1000000,
  chars_used INTEGER NOT NULL DEFAULT 0,
  -- max luồng 1–5
  max_workers INTEGER NOT NULL DEFAULT 2,
  -- max chars per chunk (0 = use default)
  max_chars INTEGER NOT NULL DEFAULT 0,
  -- split mode: line (theo dòng/paragraph) | chars (full max_chars, cắt tại , .)
  split_mode TEXT DEFAULT 'line',
  -- proxy gắn account (inline hoặc proxy_id)
  proxy_id TEXT DEFAULT '',
  proxy_provider TEXT DEFAULT 'proxyxoay_net',
  proxy_api_key TEXT DEFAULT '',
  proxy_username TEXT DEFAULT '',
  proxy_password TEXT DEFAULT '',
  proxy_host TEXT DEFAULT '',
  proxy_port INTEGER DEFAULT 0,
  proxy_label TEXT DEFAULT '',
  -- optional API key string for desktop/API clients
  api_key_hash TEXT DEFAULT '',
  api_key_prefix TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(username);
CREATE INDEX IF NOT EXISTS idx_sessions_exp ON admin_sessions(expires_at);

-- Account-Proxy many-to-many (1 account có nhiều proxy keys)
CREATE TABLE IF NOT EXISTS account_proxies (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  proxy_id TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
  FOREIGN KEY(proxy_id) REFERENCES proxies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_account_proxies_account ON account_proxies(account_id);
CREATE INDEX IF NOT EXISTS idx_account_proxies_proxy ON account_proxies(proxy_id);

-- Studio presence: online = user đang gen TTS (heartbeat)
CREATE TABLE IF NOT EXISTS presence_tokens (
  token TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  username TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_presence_tokens_acc ON presence_tokens(account_id);

CREATE TABLE IF NOT EXISTS gen_online (
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
);

CREATE INDEX IF NOT EXISTS idx_gen_online_seen ON gen_online(last_seen);
CREATE INDEX IF NOT EXISTS idx_gen_online_status ON gen_online(status);

-- seed packages
INSERT OR IGNORE INTO packages (id, name, chars, note, created_at) VALUES
  ('pkg_1m',  'Gói 1 triệu',  1000000,  '1M chars', datetime('now')),
  ('pkg_5m',  'Gói 5 triệu',  5000000,  '5M chars', datetime('now')),
  ('pkg_10m', 'Gói 10 triệu', 10000000, '10M chars', datetime('now')),
  ('pkg_50m', 'Gói 50 triệu', 50000000, '50M chars', datetime('now')),
  ('pkg_unlimited', 'Unlimited', -1, 'Không giới hạn ký tự', datetime('now'));
