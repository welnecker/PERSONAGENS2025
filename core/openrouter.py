# core/openrouter.py
from __future__ import annotations

import os
import httpx
from typing import Dict, Any, Tuple

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)

# Cabeçalhos opcionais recomendados pela OpenRouter (melhor roteamento/quotas)
OPENROUTER_SITE_URL = os.environ.get(
    "OPENROUTER_SITE_URL",
    "https://github.com/welnecker/PERSONAGENS2025",
)
OPENROUTER_APP_NAME = os.environ.get(
    "OPENROUTER_APP_NAME",
    "PERSONAGENS2025",
)

def _build_headers() -> Dict[str, str]:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY não definido no ambiente/secrets.")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    # Campos opcionais: ajudam a OpenRouter a identificar o app
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_APP_NAME:
        headers["X-Title"] = OPENROUTER_APP_NAME
    return headers


def chat(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """
    Envia uma chamada de chat para a OpenRouter (API estilo OpenAI).
    Espera payload com: model, messages, temperature, top_p, max_tokens.
    Retorna: (data_json, used_model, "openrouter")
    """
    model = payload.get("model")
    if not model:
        raise ValueError("Payload sem 'model'.")

    messages = payload.get("messages", [])
    if not isinstance(messages, list) or not messages:
        raise ValueError("Payload sem 'messages' válidas.")

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": payload.get("temperature", 0.6),
        "top_p": payload.get("top_p", 0.9),
        "max_tokens": payload.get("max_tokens", 1024),
        "stream": False,
    }

    # Repasse opcional de campos suportados, se presentes
    for k in ("stop", "frequency_penalty", "presence_penalty", "logit_bias"):
        if k in payload:
            body[k] = payload[k]

    headers = _build_headers()

    try:
        timeout = float(os.environ.get("LLM_HTTP_TIMEOUT", "60"))
        with httpx.Client(timeout=timeout) as client:
            r = client.post(OPENROUTER_BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()

            if not isinstance(data, dict) or "choices" not in data:
                raise RuntimeError(f"Resposta inesperada da OpenRouter: {data}")

            used_model = (data.get("model") or body["model"]) if isinstance(data, dict) else body["model"]
            return data, used_model, "openrouter"

    except httpx.HTTPStatusError as e:
        # Mensagens específicas úteis (429, 401, etc.)
        status = e.response.status_code if e.response else "?"
        text = ""
        try:
            text = e.response.text if e.response is not None else ""
        except Exception:
            pass
        raise RuntimeError(f"Falha OpenRouter (HTTP {status}): {text or e}") from e

    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha OpenRouter: {e}") from e

    except Exception as e:
        raise RuntimeError(f"Erro OpenRouter (genérico): {e}") from e
