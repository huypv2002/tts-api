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
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        self.ready.set()

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
        }


class ProxyPool:
    """Multi-proxy slot pool: lease → TTS → release / rotate on block."""

    def __init__(self) -> None:
        self.slots: dict[str, ProxySlot] = {}
        self._lease_cond = asyncio.Condition()
        self._started = False

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
            )
            if not slot.enabled:
                slot.state = SlotState.DISABLED
            self.slots[sid] = slot

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
            ):
                if k in data and data[k] is not None:
                    setattr(existing, k, data[k] if k != "port" else int(data[k]))
            creds_changed = (
                existing.host != prev_host
                or int(existing.port) != int(prev_port)
                or existing.username != prev_user
                or ("api_key" in data and data.get("api_key") is not None)
                or ("password" in data and data.get("password") is not None)
            )
            if not existing.enabled:
                existing.state = SlotState.DISABLED
            elif existing.state == SlotState.DISABLED or creds_changed:
                # New credentials / re-enable: leave ROTATING/DEAD so workers can lease
                existing.state = SlotState.READY
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
            )
            if not slot.enabled:
                slot.state = SlotState.DISABLED
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
        """Change exit IP; respects provider cooldown."""
        if slot.provider == "proxyxoay_net" and slot.api_key:
            deadline = time.time() + 300
            while time.time() < deadline:
                r = httpx.get(
                    PROXYXOAY_CHANGE.format(key=slot.api_key), timeout=30.0
                )
                data = r.json()
                msg = data.get("message") or ""
                if data.get("status") == 200:
                    time.sleep(4)
                    break
                m = re.search(r"(\d+)\s*giây", msg) or re.search(r"(\d+)\s*s", msg)
                wait = int(m.group(1)) + 2 if m else 15
                wait = min(max(wait, 5), 90)
                time.sleep(wait)
            else:
                slot.last_error = "rotate timeout"
        url = self.resolve_url_sync(slot)
        exit_ip = self.probe_exit_sync(url)
        slot.exit_ip = exit_ip
        slot.ok_on_ip = 0
        slot.fail_streak = 0
        slot.cooldown_until = time.time() + 5  # brief settle
        slot.state = SlotState.READY
        slot.ready.set()
        return url

    async def ensure_url(self, slot: ProxySlot) -> str:
        if slot.proxy_url:
            return slot.proxy_url
        return await asyncio.to_thread(self.resolve_url_sync, slot)

    async def lease(self, timeout: float = 120.0) -> Optional[ProxySlot]:
        """Wait for a READY slot under inflight limit."""
        settings = load_settings()
        max_inf = int(settings.get("inflight_per_proxy") or 3)
        deadline = time.time() + timeout
        while time.time() < deadline:
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
                        s.state = SlotState.COOLING
                        continue
                    if s.state == SlotState.COOLING and s.cooldown_until <= now:
                        s.state = SlotState.READY
                    if s.in_flight >= max_inf:
                        continue
                    if s.state not in (SlotState.READY, SlotState.BUSY):
                        continue
                    candidates.append(s)
                # prefer fewer in_flight, then fewer ok_on_ip (spread load)
                candidates.sort(key=lambda x: (x.in_flight, x.ok_on_ip, x.total_ok))
                if candidates:
                    slot = candidates[0]
                    slot.in_flight += 1
                    slot.state = SlotState.BUSY
                    await self.ensure_url(slot)
                    return slot
                try:
                    await asyncio.wait_for(self._lease_cond.wait(), timeout=2.0)
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
                slot.state = SlotState.READY if slot.in_flight == 0 else SlotState.BUSY
            self._lease_cond.notify_all()

    async def release_transient(self, slot: ProxySlot, err: str) -> None:
        async with self._lease_cond:
            slot.in_flight = max(0, slot.in_flight - 1)
            slot.total_fail += 1
            slot.last_error = err[:300]
            if slot.state not in (SlotState.ROTATING, SlotState.DISABLED, SlotState.DEAD):
                slot.state = SlotState.READY if slot.in_flight == 0 else SlotState.BUSY
            self._lease_cond.notify_all()

    async def release_block_and_rotate(self, slot: ProxySlot, err: str) -> None:
        """Mark blocked; single-flight rotate this slot only."""
        async with slot.lock:
            async with self._lease_cond:
                slot.in_flight = max(0, slot.in_flight - 1)
                slot.total_fail += 1
                slot.fail_streak += 1
                slot.last_error = err[:300]
                if slot.state == SlotState.ROTATING:
                    # peer already rotating
                    self._lease_cond.notify_all()
                    return
                slot.state = SlotState.ROTATING
                slot.ready.clear()
            try:
                await asyncio.to_thread(self.rotate_sync, slot)
            except Exception as e:
                slot.last_error = f"rotate fail: {e}"
                slot.state = SlotState.DEAD if slot.fail_streak >= 5 else SlotState.COOLING
                slot.cooldown_until = time.time() + 60
                slot.ready.set()
            async with self._lease_cond:
                self._lease_cond.notify_all()

    async def force_rotate(self, sid: str) -> dict:
        slot = self.slots.get(sid)
        if not slot:
            raise KeyError(sid)
        async with slot.lock:
            slot.state = SlotState.ROTATING
            slot.ready.clear()
            try:
                await asyncio.to_thread(self.rotate_sync, slot)
            except Exception as e:
                slot.last_error = str(e)
                slot.state = SlotState.READY
                slot.ready.set()
                raise
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
