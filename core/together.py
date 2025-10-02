# core/together.py
from __future__ import annotations
import os, httpx
from typing import Dict, Any, Tuple

TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY", "")
TOGETHER_BASE_URL = os.environ.get(
    "TOGETHER_BASE_URL", "https://api.together.xyz/v1/chat/completions"
)

def _headers() -> Dict[str, str]:
    if not TOGETHER_API_KEY:
        raise RuntimeError("TOGETHER_API_KEY não definido.")
    return {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }

def chat(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    model = payload.get("model")
    if not model:
        raise ValueError("Payload sem 'model'.")
    messages = payload.get("messages") or []
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
    for k in ("stop", "frequency_penalty", "presence_penalty", "logit_bias"):
        if k in payload:
            body[k] = payload[k]

    try:
        timeout = float(os.environ.get("LLM_HTTP_TIMEOUT", "60"))
        with httpx.Client(timeout=timeout) as client:
            r = client.post(TOGETHER_BASE_URL, json=body, headers=_headers())
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or "choices" not in data:
                raise RuntimeError(f"Resposta inesperada Together: {data}")
            used = body["model"]
            return data, used, "together"
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response else "?"
        text = ""
        try:
            text = e.response.text if e.response is not None else ""
        except Exception:
            pass
        raise RuntimeError(f"Falha Together (HTTP {status}): {text or e}") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha Together: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Erro Together (genérico): {e}") from e
