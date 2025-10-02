# core/together.py
from __future__ import annotations

import os
import httpx
from typing import Dict, Any, Tuple

TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY", "")
TOGETHER_BASE_URL = os.environ.get(
    "TOGETHER_BASE_URL",
    "https://api.together.xyz/v1/chat/completions",
)

def chat(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """
    Envia uma chamada de chat ao Together (API estilo OpenAI).
    Espera payload com: model, messages, temperature, top_p, max_tokens.
    Retorna: (data_json, used_model, "together")
    """
    if not TOGETHER_API_KEY:
        raise RuntimeError("TOGETHER_API_KEY não definido no ambiente/secrets.")

    model = payload.get("model")
    if not model:
        raise ValueError("Payload sem 'model'.")

    body = {
        "model": model,
        "messages": payload.get("messages", []),
        "temperature": payload.get("temperature", 0.7),
        "top_p": payload.get("top_p", 0.9),
        "max_tokens": payload.get("max_tokens", 1024),
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        timeout = float(os.environ.get("LLM_HTTP_TIMEOUT", "60"))
        with httpx.Client(timeout=timeout) as client:
            r = client.post(TOGETHER_BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()

            # Nome do modelo usado (algumas respostas não trazem essa chave)
            used_model = (data.get("model") or body["model"]) if isinstance(data, dict) else body["model"]

            # Validação básica do formato OpenAI-like
            if not isinstance(data, dict) or "choices" not in data:
                raise RuntimeError(f"Resposta inesperada do Together: {data}")

            return data, used_model, "together"

    except httpx.HTTPError as e:
        # Propaga como RuntimeError para o chamador lidar igual aos outros providers
        raise RuntimeError(f"Falha Together: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Erro Together (genérico): {e}") from e
