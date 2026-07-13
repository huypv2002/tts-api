from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TtsRequest(BaseModel):
    model_config = {"populate_by_name": True}

    text: str = Field(..., min_length=1, max_length=20000)
    voice: Optional[str] = None
    model: Optional[str] = None
    language_code: Optional[str] = Field(None, alias="lang")
    speed: float = 1.0
    wait: bool = False  # sync wait for result (short timeout)


class AdminLogin(BaseModel):
    password: str


class ApiKeyCreate(BaseModel):
    name: str = "default"
    max_chars: Optional[int] = None
    quota_chars_day: Optional[int] = None
    quota_jobs_day: Optional[int] = None
    max_concurrent: Optional[int] = None
    note: str = ""
    # optional dedicated proxyxoay line for this account/key
    proxy_provider: Optional[str] = "proxyxoay_net"
    proxy_api_key: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_label: Optional[str] = None


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    max_chars: Optional[int] = None
    quota_chars_day: Optional[int] = None
    quota_jobs_day: Optional[int] = None
    max_concurrent: Optional[int] = None
    note: Optional[str] = None
    proxy_provider: Optional[str] = None
    proxy_api_key: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_label: Optional[str] = None


class SettingsUpdate(BaseModel):
    default_max_chars: Optional[int] = None
    hard_max_chars: Optional[int] = None
    default_quota_chars_day: Optional[int] = None
    default_quota_jobs_day: Optional[int] = None
    default_max_concurrent: Optional[int] = None
    inflight_per_proxy: Optional[int] = None
    worker_count: Optional[int] = None
    public_base_url: Optional[str] = None
    default_voice: Optional[str] = None
    default_model: Optional[str] = None
    default_lang: Optional[str] = None
    admin_password: Optional[str] = None
    # when true: copy default_max_chars / quotas onto ALL existing API keys
    apply_to_all_keys: Optional[bool] = False


class ProxyUpsert(BaseModel):
    id: Optional[str] = None
    label: str = ""
    enabled: bool = True
    provider: str = "proxyxoay_net"
    api_key: str = ""
    username: str = ""
    password: str = ""
    host: str = ""
    port: int = 8570
