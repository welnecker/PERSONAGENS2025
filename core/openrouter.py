# core/openrouter.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple
from urllib import request, error


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return json.loads(body.decode("utf-8"))
    except error.HTTPError as e:
        body = (e.read() or b"").decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenRouter HTTPError {e.code}: {body}") from e
    except error.URLError as e:
        raise RuntimeError(f"OpenRouter URLError: {e}") from e


def chat(
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Chamada compatível com o restante do app.
    Retorna (data_json, used_model, provider).
    """
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY ausente")

    url = (base_url or "https://openrouter.ai") + "/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Os dois abaixo são recomendados pelo OpenRouter, mas opcionais
        "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://streamlit.io"),
        "X-Title": os.getenv("OPENROUTER_APP_NAME", "personagens2025"),
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
    }
    data = _post_json(url, headers, payload)
    used = data.get("model") or model
    return data, used, "openrouter"
