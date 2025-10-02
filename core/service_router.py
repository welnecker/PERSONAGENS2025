# core/service_router.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple
from urllib import request, error

# Import protegido: não quebra se o módulo não existir
try:
    from .openrouter import chat as openrouter_chat  # type: ignore
except Exception:  # pragma: no cover
    openrouter_chat = None  # fallback ativará outro provedor


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        return json.loads(body.decode("utf-8"))


def _openai_chat_rest(
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> Tuple[Dict[str, Any], str, str]:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY ausente")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }
    data = _post_json(url, headers, payload)
    used = data.get("model") or model
    return data, used, "openai"


def _echo_fallback(messages: List[Dict[str, str]]) -> Tuple[Dict[str, Any], str, str]:
    """
    Fallback local para não travar o app quando nenhuma chave está configurada.
    Devolve um texto curto ecoando a última mensagem do usuário.
    """
    last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    content = f"(modo offline) Eu ouvi: {last_user[:400]}"
    data = {"choices": [{"message": {"content": content}}], "model": "offline-echo"}
    return data, "offline-echo", "local"


def route_chat_strict(model: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """
    Ponto único de roteamento. Tenta OpenRouter, depois OpenAI REST, depois fallback local.
    Retorna (data_json, used_model, provider).
    """
    messages: List[Dict[str, str]] = payload.get("messages", [])
    max_tokens: int = int(payload.get("max_tokens", 2048))
    temperature: float = float(payload.get("temperature", 0.7))
    top_p: float = float(payload.get("top_p", 0.9))

    # 1) OpenRouter se disponível
    if openrouter_chat and os.getenv("OPENROUTER_API_KEY"):
        try:
            return openrouter_chat(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
        except Exception:
            # cai para o próximo provedor
            pass

    # 2) OpenAI REST se disponível
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _openai_chat_rest(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
        except Exception:
            # cai para o fallback
            pass

    # 3) Fallback local (não quebra a aplicação)
    return _echo_fallback(messages)

