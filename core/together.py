# core/together.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Optional

TOGETHER_BASE_URL = "https://api.together.xyz/v1/chat/completions"

def _headers() -> Dict[str, str]:
    key = os.getenv("TOGETHER_API_KEY")
    if not key:
        raise RuntimeError("TOGETHER_API_KEY não definido.")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def chat(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Chama Together Chat Completions. Retorna (data, used_model, "together").
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
            r = client.post(TOGETHER_BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            used = body["model"]
            return data, used, "together"
    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha Together: {e}") from e
