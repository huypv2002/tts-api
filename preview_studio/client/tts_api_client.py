# -*- coding: utf-8 -*-
"""HTTP client for tts-api (public + admin)."""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx


class TtsApiClient:
    def __init__(self, base_url: str, api_key: str = "", admin_token: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.admin_token = (admin_token or "").strip()

    def _headers_public(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _headers_admin(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.admin_token:
            h["X-Admin-Token"] = self.admin_token
        return h

    def health(self) -> dict:
        with httpx.Client(timeout=20.0) as c:
            r = c.get(f"{self.base_url}/v1/health")
            r.raise_for_status()
            return r.json()

    def me(self) -> dict:
        with httpx.Client(timeout=20.0) as c:
            r = c.get(f"{self.base_url}/v1/me", headers=self._headers_public())
            r.raise_for_status()
            return r.json()

    def admin_login(self, password: str) -> str:
        with httpx.Client(timeout=20.0) as c:
            r = c.post(
                f"{self.base_url}/admin/api/login",
                json={"password": password},
            )
            r.raise_for_status()
            token = r.json().get("token") or ""
            self.admin_token = token
            return token

    def list_keys(self) -> list[dict]:
        with httpx.Client(timeout=30.0) as c:
            r = c.get(
                f"{self.base_url}/admin/api/keys",
                headers=self._headers_admin(),
            )
            r.raise_for_status()
            return (r.json() or {}).get("keys") or []

    def create_key(self, **body: Any) -> dict:
        with httpx.Client(timeout=30.0) as c:
            r = c.post(
                f"{self.base_url}/admin/api/keys",
                headers=self._headers_admin(),
                json=body,
            )
            r.raise_for_status()
            return r.json()

    def patch_key(self, key_id: int, **body: Any) -> dict:
        with httpx.Client(timeout=30.0) as c:
            r = c.patch(
                f"{self.base_url}/admin/api/keys/{key_id}",
                headers=self._headers_admin(),
                json=body,
            )
            r.raise_for_status()
            return r.json()

    def list_proxies(self) -> dict:
        with httpx.Client(timeout=30.0) as c:
            r = c.get(
                f"{self.base_url}/admin/api/proxies",
                headers=self._headers_admin(),
            )
            r.raise_for_status()
            return r.json()

    def upsert_proxy(self, body: dict) -> dict:
        with httpx.Client(timeout=30.0) as c:
            r = c.post(
                f"{self.base_url}/admin/api/proxies",
                headers=self._headers_admin(),
                json=body,
            )
            r.raise_for_status()
            return r.json()

    def create_tts(
        self,
        text: str,
        lang: str = "en",
        voice: Optional[str] = None,
        model: Optional[str] = None,
        speed: float = 1.0,
        wait: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            "text": text,
            "lang": lang,
            "speed": speed,
            "wait": wait,
        }
        if voice:
            payload["voice"] = voice
        if model:
            payload["model"] = model
        with httpx.Client(timeout=120.0) as c:
            r = c.post(
                f"{self.base_url}/v1/tts",
                headers=self._headers_public(),
                json=payload,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"TTS HTTP {r.status_code}: {r.text[:300]}")
            return r.json()

    def job_status(self, job_id: str) -> dict:
        with httpx.Client(timeout=30.0) as c:
            r = c.get(
                f"{self.base_url}/v1/tts/{job_id}",
                headers=self._headers_public(),
            )
            r.raise_for_status()
            return r.json()

    def download_audio(self, job_id: str, out_path: str) -> str:
        with httpx.Client(timeout=120.0) as c:
            r = c.get(
                f"{self.base_url}/v1/tts/{job_id}/audio",
                headers=self._headers_public(),
            )
            r.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(r.content)
        return out_path

    def synthesize_to_file(
        self,
        text: str,
        out_path: str,
        lang: str = "en",
        voice: Optional[str] = None,
        model: Optional[str] = None,
        speed: float = 1.0,
        poll_interval: float = 1.5,
        poll_timeout: float = 900.0,
    ) -> dict:
        """Create job, poll until done, save mp3. Returns status dict."""
        job = self.create_tts(
            text=text, lang=lang, voice=voice, model=model, speed=speed, wait=False
        )
        jid = job.get("id")
        if not jid:
            raise RuntimeError(f"no job id: {job}")
        t0 = time.time()
        last: dict = job
        while time.time() - t0 < poll_timeout:
            last = self.job_status(jid)
            st = last.get("status")
            if st == "done":
                self.download_audio(jid, out_path)
                last["local_path"] = out_path
                return last
            if st == "failed":
                raise RuntimeError(last.get("error") or "job failed")
            time.sleep(poll_interval)
        raise RuntimeError(
            f"poll timeout after {poll_timeout}s status={last.get('status')}"
        )
