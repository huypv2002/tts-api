from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx

from ..config import load_proxies_file, save_proxies_file, load_settings

PROXYXOAY_STATUS = "https://proxyxoay.net/api/rotating-proxy/key-status/{key}"
PROXYXOAY_CHANGE = "https://proxyxoay.net/api/rotating-proxy/change-key-ip/{key}"

# How long a slot may stay ROTATING before auto-heal
ROTATING_STUCK_S = 180.0
DEAD_COOLDOWN_S = 90.0


class SlotState(str, Enum):
    READY = "ready"
    BUSY = "busy"
    ROTATING = "rotating"
    COOLING = "cooling"
    DEAD = "dead"
    DISABLED = "disabled"


@dataclass
class ProxySlot:
    id: str
    label: str
    enabled: bool
    provider: str
    api_key: str
    username: str
    password: str
    host: str
    port: int
    # proxyxoay.shop fields
    shop_nhamang: str = "random"
    shop_tinhthanh: int = 0
    shop_whitelist: str = ""
    shop_method: str = "GET"
    state: SlotState = SlotState.READY
    proxy_url: str = ""
    exit_ip: str = ""
    ok_on_ip: int = 0
    in_flight: int = 0
    fail_streak: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    total_ok: int = 0
    total_fail: int = 0
    state_since: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        self.ready.set()

    def set_state(self, st: SlotState) -> None:
        if self.state != st:
            self.state = st
            self.state_since = time.time()

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "enabled": self.enabled,
            "provider": self.provider,
            "host": self.host,
            "port": self.port,
            "state": self.state.value,
            "exit_ip": self.exit_ip,
            "ok_on_ip": self.ok_on_ip,
            "in_flight": self.in_flight,
            "fail_streak": self.fail_streak,
            "cooldown_until": self.cooldown_until,
            "last_error": self.last_error[:200],
            "total_ok": self.total_ok,
            "total_fail": self.total_fail,
            "has_api_key": bool(self.api_key),
            "username": self.username[:3] + "***" if self.username else "",
            "state_age_s": round(time.time() - self.state_since, 1),
            "shop_nhamang": self.shop_nhamang,
            "shop_tinhthanh": self.shop_tinhthanh,
            "shop_whitelist": self.shop_whitelist,
            "shop_method": self.shop_method,
        }


class ProxyPool:
    """
    Multi-proxy slot pool for near-100% job success:

      lease → TTS → release_ok
                 → release_transient (retry same IP)
                 → release_block_and_rotate (new exit IP, same job retries)

    Auto-heal stuck ROTATING/DEAD so workers are never blocked forever.
    """

    def __init__(self) -> None:
        self.slots: dict[str, ProxySlot] = {}
        self._lease_cond = asyncio.Condition()
        self._started = False
        self._heal_task: Optional[asyncio.Task] = None

    def load(self) -> None:
        rows = load_proxies_file()
        self.slots.clear()
        for r in rows:
            sid = str(r.get("id") or f"px{len(self.slots)+1}")
            slot = ProxySlot(
                id=sid,
                label=r.get("label") or sid,
                enabled=bool(r.get("enabled", True)),
                provider=r.get("provider") or "proxyxoay_net",
                api_key=(r.get("api_key") or "").strip(),
                username=(r.get("username") or "").strip(),
                password=(r.get("password") or "").strip(),
                host=(r.get("host") or "").strip(),
                port=int(r.get("port") or 8570),
                shop_nhamang=r.get("shop_nhamang") or "random",
                shop_tinhthanh=int(r.get("shop_tinhthanh") or 0),
                shop_whitelist=r.get("shop_whitelist") or "",
                shop_method=r.get("shop_method") or "GET",
            )
            if not slot.enabled:
                slot.set_state(SlotState.DISABLED)
            self.slots[sid] = slot

    def start_background(self) -> None:
        """Optional periodic heal (called from worker manager)."""
        if self._heal_task is None or self._heal_task.done():
            self._heal_task = asyncio.create_task(self._heal_loop(), name="proxy-heal")

    async def _heal_loop(self) -> None:
        while True:
            try:
                n = await self.heal_stuck_slots()
                if n:
                    print(f"[proxy-pool] healed {n} stuck slot(s)", flush=True)
            except Exception as e:
                print(f"[proxy-pool] heal error: {e}", flush=True)
            await asyncio.sleep(15.0)

    def save_config(self) -> None:
        rows = []
        for s in self.slots.values():
            rows.append(
                {
                    "id": s.id,
                    "label": s.label,
                    "enabled": s.enabled,
                    "provider": s.provider,
                    "api_key": s.api_key,
                    "username": s.username,
                    "password": s.password,
                    "host": s.host,
                    "port": s.port,
                    "shop_nhamang": s.shop_nhamang,
                    "shop_tinhthanh": s.shop_tinhthanh,
                    "shop_whitelist": s.shop_whitelist,
                    "shop_method": s.shop_method,
                }
            )
        save_proxies_file(rows)

    def list_public(self) -> list[dict]:
        return [s.to_public() for s in self.slots.values()]

    def upsert(self, data: dict) -> ProxySlot:
        sid = str(data.get("id") or f"px{int(time.time())}")
        existing = self.slots.get(sid)
        if existing:
            prev_host = existing.host
            prev_port = existing.port
            prev_user = existing.username
            for k in (
                "label",
                "enabled",
                "provider",
                "api_key",
                "username",
                "password",
                "host",
                "port",
                "shop_nhamang",
                "shop_tinhthanh",
                "shop_whitelist",
                "shop_method",
            ):
                if k in data and data[k] is not None:
                    if k == "port":
                        setattr(existing, k, int(data[k]))
                    elif k == "shop_tinhthanh":
                        setattr(existing, k, int(data[k]))
                    else:
                        setattr(existing, k, data[k])
            creds_changed = (
                existing.host != prev_host
                or int(existing.port) != int(prev_port)
                or existing.username != prev_user
                or ("api_key" in data and data.get("api_key") is not None)
                or ("password" in data and data.get("password") is not None)
            )
            if not existing.enabled:
                existing.set_state(SlotState.DISABLED)
            elif existing.state == SlotState.DISABLED or creds_changed:
                existing.set_state(SlotState.READY)
                existing.proxy_url = ""
                existing.exit_ip = ""
                existing.fail_streak = 0
                existing.cooldown_until = 0.0
                existing.last_error = ""
                existing.ready.set()
            slot = existing
        else:
            slot = ProxySlot(
                id=sid,
                label=data.get("label") or sid,
                enabled=bool(data.get("enabled", True)),
                provider=data.get("provider") or "proxyxoay_net",
                api_key=(data.get("api_key") or "").strip(),
                username=(data.get("username") or "").strip(),
                password=(data.get("password") or "").strip(),
                host=(data.get("host") or "").strip(),
                port=int(data.get("port") or 8570),
                shop_nhamang=data.get("shop_nhamang") or "random",
                shop_tinhthanh=int(data.get("shop_tinhthanh") or 0),
                shop_whitelist=data.get("shop_whitelist") or "",
                shop_method=data.get("shop_method") or "GET",
            )
            if not slot.enabled:
                slot.set_state(SlotState.DISABLED)
            self.slots[sid] = slot
        self.save_config()
        return slot

    def delete(self, sid: str) -> bool:
        if sid in self.slots:
            del self.slots[sid]
            self.save_config()
            return True
        return False

    def _build_url(self, slot: ProxySlot) -> str:
        if slot.username and slot.password and slot.host and slot.port:
            return f"http://{slot.username}:{slot.password}@{slot.host}:{slot.port}"
        if slot.host and slot.port:
            return f"http://{slot.host}:{slot.port}"
        return ""

    def resolve_url_sync(self, slot: ProxySlot) -> str:
        """Refresh URL from proxyxoay status when possible."""
        if slot.provider == "proxyxoay_net" and slot.api_key:
            try:
                r = httpx.get(
                    PROXYXOAY_STATUS.format(key=slot.api_key), timeout=20.0
                )
                data = r.json()
                if data.get("status") == 200:
                    d = data.get("data") or {}
                    conn = d.get("proxy_connection") or {}
                    host = conn.get("ip") or slot.host
                    port = conn.get("http_ipv4") or slot.port
                    auth = d.get("authentication") or ""
                    user = d.get("username") or (
                        auth.split(":")[0] if ":" in auth else slot.username
                    )
                    pw = d.get("password") or (
                        auth.split(":")[1] if ":" in auth else slot.password
                    )
                    if host and port and str(port) not in ("-1", "0", ""):
                        slot.host = str(host)
                        slot.port = int(port)
                        if user:
                            slot.username = user
                        if pw:
                            slot.password = pw
            except Exception as e:
                slot.last_error = f"status: {e}"
        elif slot.provider == "proxyxoay_shop" and slot.api_key:
            # proxyxoay.shop: gọi get.php để lấy proxy URL mới
            try:
                from fast_tts import proxyxoay_shop_from_key
                url = proxyxoay_shop_from_key(
                    slot.api_key,
                    nhamang=slot.shop_nhamang or "random",
                    tinhthanh=slot.shop_tinhthanh or 0,
                    whitelist=slot.shop_whitelist or "",
                    method=slot.shop_method or "GET",
                )
                # Parse URL để extract host/port/username/password
                # URL format: http://user:pass@host:port
                if url.startswith("http://"):
                    url = url[7:]
                if "@" in url:
                    auth, hostport = url.rsplit("@", 1)
                    if ":" in auth:
                        slot.username, slot.password = auth.split(":", 1)
                else:
                    hostport = url
                if ":" in hostport:
                    h, p = hostport.rsplit(":", 1)
                    slot.host = h
                    try:
                        slot.port = int(p)
                    except ValueError:
                        pass
            except Exception as e:
                slot.last_error = f"shop: {e}"
        url = self._build_url(slot)
        slot.proxy_url = url
        return url

    def probe_exit_sync(self, proxy_url: str, timeout: float = 12.0) -> str:
        if not proxy_url:
            return ""
        try:
            r = httpx.get(
                "https://api.ipify.org?format=json",
                proxy=proxy_url,
                timeout=timeout,
            )
            return (r.json() or {}).get("ip") or ""
        except Exception:
            try:
                r = httpx.get(
                    "https://httpbin.org/ip", proxy=proxy_url, timeout=timeout
                )
                return (r.json() or {}).get("origin", "").split(",")[0].strip()
            except Exception:
                return ""

    def rotate_sync(self, slot: ProxySlot) -> str:
        """
        Change exit IP; respects provider cooldown.
        Always ends READY if tunnel works, even when change-ip is rate-limited
        (re-use current exit after wait so jobs are not stuck forever).
        """
        changed = False
        if slot.provider == "proxyxoay_net" and slot.api_key:
            # shorter budget than before so heal/lease don't block 5 min
            deadline = time.time() + 120
            while time.time() < deadline:
                try:
                    r = httpx.get(
                        PROXYXOAY_CHANGE.format(key=slot.api_key), timeout=25.0
                    )
                    data = r.json()
                except Exception as e:
                    slot.last_error = f"change-ip: {e}"
                    time.sleep(5)
                    continue
                msg = str(data.get("message") or "")
                if data.get("status") == 200:
                    time.sleep(3)
                    changed = True
                    break
                m = re.search(r"(\d+)\s*giây", msg) or re.search(
                    r"(\d+)\s*(?:s|sec)", msg, re.I
                )
                wait = int(m.group(1)) + 2 if m else 12
                wait = min(max(wait, 3), 60)
                slot.last_error = f"change-ip wait {wait}s: {msg[:80]}"
                time.sleep(wait)
            if not changed:
                slot.last_error = (slot.last_error or "") + " | rotate soft-timeout"
        elif slot.provider == "proxyxoay_shop" and slot.api_key:
            # proxyxoay.shop: mỗi lần gọi get.php = 1 IP mới (không cần change-ip)
            # resolve_url_sync sẽ tự gọi get.php để lấy URL mới
            changed = True

        url = self.resolve_url_sync(slot)
        exit_ip = self.probe_exit_sync(url, timeout=15.0)
        slot.exit_ip = exit_ip
        slot.ok_on_ip = 0
        if exit_ip:
            slot.fail_streak = 0
            slot.cooldown_until = time.time() + 3
            slot.set_state(SlotState.READY)
            slot.ready.set()
        else:
            # tunnel dead — cool then retry later
            slot.fail_streak += 1
            slot.cooldown_until = time.time() + min(30 * slot.fail_streak, 120)
            slot.set_state(
                SlotState.DEAD if slot.fail_streak >= 8 else SlotState.COOLING
            )
            slot.ready.set()
            slot.last_error = (slot.last_error or "") + " | probe empty after rotate"
        return url

    async def ensure_url(self, slot: ProxySlot, force: bool = False) -> str:
        if slot.proxy_url and not force:
            return slot.proxy_url
        return await asyncio.to_thread(self.resolve_url_sync, slot)

    async def heal_stuck_slots(self) -> int:
        """
        Unstick ROTATING (timeout) and revive DEAD/COOLING past cooldown.
        Returns number of slots healed.
        """
        healed = 0
        now = time.time()
        for slot in list(self.slots.values()):
            if not slot.enabled:
                continue
            age = now - slot.state_since
            # revive cooling
            if slot.state == SlotState.COOLING and slot.cooldown_until <= now:
                slot.set_state(SlotState.READY)
                slot.ready.set()
                healed += 1
                continue
            # revive dead after cooldown
            if slot.state == SlotState.DEAD and age >= DEAD_COOLDOWN_S:
                if slot.lock.locked():
                    continue
                try:
                    async with slot.lock:
                        slot.set_state(SlotState.ROTATING)
                        slot.ready.clear()
                        await asyncio.to_thread(self.rotate_sync, slot)
                        healed += 1
                except Exception as e:
                    slot.last_error = f"heal dead: {e}"
                    slot.set_state(SlotState.COOLING)
                    slot.cooldown_until = now + 60
                    slot.ready.set()
                continue
            # stuck rotating
            if slot.state == SlotState.ROTATING and age >= ROTATING_STUCK_S:
                if slot.lock.locked():
                    continue
                try:
                    async with slot.lock:
                        print(
                            f"[proxy-pool] unstick {slot.id} rotating {age:.0f}s",
                            flush=True,
                        )
                        await asyncio.to_thread(self.rotate_sync, slot)
                        healed += 1
                except Exception as e:
                    slot.last_error = f"unstick: {e}"
                    slot.set_state(SlotState.COOLING)
                    slot.cooldown_until = now + 30
                    slot.ready.set()
                    healed += 1
        if healed:
            async with self._lease_cond:
                self._lease_cond.notify_all()
        return healed

    async def lease(self, timeout: float = 60.0) -> Optional[ProxySlot]:
        """Wait for a READY slot under inflight limit. Heals stuck slots while waiting."""
        settings = load_settings()
        max_inf = int(settings.get("inflight_per_proxy") or 3)
        deadline = time.time() + timeout
        last_heal = 0.0
        while time.time() < deadline:
            now = time.time()
            if now - last_heal > 10:
                await self.heal_stuck_slots()
                last_heal = now
            async with self._lease_cond:
                now = time.time()
                candidates = []
                for s in self.slots.values():
                    if not s.enabled or s.state in (
                        SlotState.DISABLED,
                        SlotState.DEAD,
                        SlotState.ROTATING,
                    ):
                        continue
                    if s.cooldown_until > now:
                        if s.state != SlotState.COOLING:
                            s.set_state(SlotState.COOLING)
                        continue
                    if s.state == SlotState.COOLING and s.cooldown_until <= now:
                        s.set_state(SlotState.READY)
                    if s.in_flight >= max_inf:
                        continue
                    if s.state not in (SlotState.READY, SlotState.BUSY):
                        continue
                    candidates.append(s)
                candidates.sort(key=lambda x: (x.in_flight, x.ok_on_ip, x.total_fail))
                if candidates:
                    slot = candidates[0]
                    slot.in_flight += 1
                    slot.set_state(SlotState.BUSY)
                    try:
                        await self.ensure_url(slot)
                    except Exception as e:
                        slot.last_error = str(e)[:200]
                    if not slot.proxy_url:
                        # cannot use — release and cool
                        slot.in_flight = max(0, slot.in_flight - 1)
                        slot.set_state(SlotState.COOLING)
                        slot.cooldown_until = time.time() + 10
                        continue
                    return slot
                try:
                    await asyncio.wait_for(self._lease_cond.wait(), timeout=1.5)
                except asyncio.TimeoutError:
                    pass
        return None

    async def release_ok(self, slot: ProxySlot) -> None:
        async with self._lease_cond:
            slot.in_flight = max(0, slot.in_flight - 1)
            slot.ok_on_ip += 1
            slot.total_ok += 1
            slot.fail_streak = 0
            if slot.state not in (SlotState.ROTATING, SlotState.DISABLED, SlotState.DEAD):
                slot.set_state(
                    SlotState.READY if slot.in_flight == 0 else SlotState.BUSY
                )
            self._lease_cond.notify_all()

    async def release_transient(self, slot: ProxySlot, err: str) -> None:
        async with self._lease_cond:
            slot.in_flight = max(0, slot.in_flight - 1)
            slot.total_fail += 1
            slot.fail_streak += 1
            slot.last_error = err[:300]
            if slot.state not in (SlotState.ROTATING, SlotState.DISABLED, SlotState.DEAD):
                slot.set_state(
                    SlotState.READY if slot.in_flight == 0 else SlotState.BUSY
                )
            self._lease_cond.notify_all()

    async def release_block_and_rotate(self, slot: ProxySlot, err: str) -> None:
        """Mark blocked; single-flight rotate this slot only. Always recovers state."""
        async with slot.lock:
            async with self._lease_cond:
                slot.in_flight = max(0, slot.in_flight - 1)
                slot.total_fail += 1
                slot.fail_streak += 1
                slot.last_error = err[:300]
                if slot.state == SlotState.ROTATING:
                    self._lease_cond.notify_all()
                    return
                slot.set_state(SlotState.ROTATING)
                slot.ready.clear()
            try:
                await asyncio.to_thread(self.rotate_sync, slot)
            except Exception as e:
                slot.last_error = f"rotate fail: {e}"
                slot.set_state(
                    SlotState.DEAD if slot.fail_streak >= 8 else SlotState.COOLING
                )
                slot.cooldown_until = time.time() + 45
                slot.ready.set()
            async with self._lease_cond:
                self._lease_cond.notify_all()

    async def force_rotate(self, sid: str) -> dict:
        slot = self.slots.get(sid)
        if not slot:
            raise KeyError(sid)
        async with slot.lock:
            slot.set_state(SlotState.ROTATING)
            slot.ready.clear()
            try:
                await asyncio.to_thread(self.rotate_sync, slot)
            except Exception as e:
                slot.last_error = str(e)
                slot.set_state(SlotState.READY)
                slot.ready.set()
                raise
        async with self._lease_cond:
            self._lease_cond.notify_all()
        return slot.to_public()

    def stats(self) -> dict:
        by = {}
        for s in self.slots.values():
            by[s.state.value] = by.get(s.state.value, 0) + 1
        return {
            "total": len(self.slots),
            "by_state": by,
            "ready": sum(
                1
                for s in self.slots.values()
                if s.enabled and s.state in (SlotState.READY, SlotState.BUSY)
            ),
        }


# singleton
pool = ProxyPool()
