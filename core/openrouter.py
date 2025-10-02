# core/openrouter.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Optional

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

def _headers() -> Dict[str, str]:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY não definido.")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # headers opcionais
    site = os.getenv("OPENROUTER_SITE_URL")
    app = os.getenv("OPENROUTER_APP_NAME")
    if site:
        headers["HTTP-Referer"] = site
        headers["X-Title"] = app or "PERSONAGENS2025"
    return headers

def chat(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Chama OpenRouter Chat Completions. Retorna (data, used_model, "openrouter").
    Importa httpx de forma preguiçosa para não quebrar o app se o pacote não estiver instalado.
    """
    try:
        import httpx  # lazy import
    except Exception as e:
        raise RuntimeError("Pacote `httpx` não está instalado. Adicione em requirements.txt.") from e

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }
    if extra_params:
        body.update(extra_params)

    headers = _headers()
    timeout = float(os.getenv("LLM_HTTP_TIMEOUT", "60"))

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(OPENROUTER_BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            used = body["model"]
            return data, used, "openrouter"
    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha OpenRouter: {e}") from e
