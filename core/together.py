from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import httpx

DEFAULT_MODELS = [
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
    "together/Qwen/Qwen2.5-72B-Instruct",
    "together/Qwen/QwQ-32B",
]

BASE_URL = os.environ.get("TOGETHER_BASE_URL", "https://api.together.xyz/v1/chat/completions")


def _strip_prefix(model: str) -> str:
    # UI usa "together/<nome-do-modelo>" — a API não aceita esse prefixo
    return model.split("/", 1)[1] if model.startswith("together/") else model


def chat(model: str,
         messages: List[Dict[str, str]],
         max_tokens: int = 1024,
         temperature: float = 0.7,
         top_p: float = 0.95,
         **_: Any) -> Tuple[Dict[str, Any], str, str]:
    api_key = os.environ.get("TOGETHER_API_KEY")
    if not api_key:
        raise RuntimeError("Together sem API key (TOGETHER_API_KEY).")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "model": _strip_prefix(model),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }

    try:
        timeout = float(os.environ.get("LLM_HTTP_TIMEOUT", "60"))
        transport = httpx.HTTPTransport(retries=2)
        with httpx.Client(timeout=timeout, transport=transport) as client:
            r = client.post(BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                raise httpx.HTTPStatusError(str(data["error"]), request=r.request, response=r)
            return data, model, "together"
    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha Together: {e}") from e
