from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import httpx

DEFAULT_MODELS = [
    "deepseek/deepseek-chat-v3-0324",
    "anthropic/claude-3.5-haiku",
    "qwen/qwen3-max",
    "nousresearch/hermes-3-llama-3.1-405b",
]

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")


def chat(model: str,
         messages: List[Dict[str, str]],
         max_tokens: int = 1024,
         temperature: float = 0.7,
         top_p: float = 0.95,
         **_: Any) -> Tuple[Dict[str, Any], str, str]:
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_TOKEN")
    if not api_key:
        raise RuntimeError("OpenRouter sem API key (OPENROUTER_API_KEY).")

    referer = os.environ.get("APP_PUBLIC_URL") or "https://github.com/welnecker/PERSONAGENS2025"
    title   = os.environ.get("APP_NAME", "PERSONAGENS2025")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": referer,
        "X-Title": title,
    }
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }

    try:
        timeout = float(os.environ.get("LLM_HTTP_TIMEOUT", "60"))
        # com pequenas tentativas ajuda em intermitências
        transport = httpx.HTTPTransport(retries=2)
        with httpx.Client(timeout=timeout, transport=transport) as client:
            r = client.post(BASE_URL, json=body, headers=headers)
            # Se não for 2xx, levanta para UI mostrar
            r.raise_for_status()
            data = r.json()
            # OpenRouter manda {"error": {...}} em 200 às vezes — trate aqui
            if isinstance(data, dict) and data.get("error"):
                raise httpx.HTTPStatusError(str(data["error"]), request=r.request, response=r)
            return data, model, "openrouter"
    except httpx.HTTPError as e:
        # Deixa a UI ver o erro real
        raise RuntimeError(f"Falha OpenRouter: {e}") from e
