# core/models.py
from __future__ import annotations
import os, re
from typing import List

OPENROUTER_DEFAULT = [
    "anthropic/claude-3.5-sonnet",
    "openai/gpt-4o-mini",
    "google/gemini-1.5-pro",
    "openrouter/auto",
]

TOGETHER_DEFAULT = [
    "meta-llama/Llama-3.1-70B-Instruct-Turbo",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "Qwen/Qwen2.5-72B-Instruct-Turbo",
]

def _env_list(var: str, defaults: List[str]) -> List[str]:
    raw = os.getenv(var, "") or ""
    if raw.strip():
        parts = [p.strip() for p in re.split(r"[,\n;]+", raw) if p.strip()]
        return parts or defaults
    return defaults

def list_models(provider: str) -> List[str]:
    p = (provider or "").lower()
    if p == "openrouter":
        return _env_list("OPENROUTER_MODELS", OPENROUTER_DEFAULT)
    if p == "together":
        return _env_list("TOGETHER_MODELS", TOGETHER_DEFAULT)
    return []

def available_providers() -> List[str]:
    # Mostra apenas provedores com chave configurada
    out: List[str] = []
    if os.getenv("OPENROUTER_API_KEY"):
        out.append("openrouter")
    if os.getenv("TOGETHER_API_KEY"):
        out.append("together")
    return out or ["openrouter", "together"]  # fallback visual (mesmo sem chave)
