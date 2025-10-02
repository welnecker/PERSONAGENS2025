from __future__ import annotations

import os
from typing import Dict, List, Tuple, Any

from .openrouter import chat as openrouter_chat, DEFAULT_MODELS as OR_MODELS
from .together import chat as together_chat, DEFAULT_MODELS as TG_MODELS


def available_providers() -> List[Tuple[str, bool, str]]:
    """
    [(nome, configurado, detalhe)]
    """
    have_or = bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_TOKEN"))
    have_tg = bool(os.getenv("TOGETHER_API_KEY"))
    return [
        ("OpenRouter", have_or, "OK" if have_or else "sem chave"),
        ("Together",   have_tg, "OK" if have_tg else "sem chave"),
    ]


def list_models(provider: str | None = None) -> List[str]:
    if provider == "OpenRouter":
        return OR_MODELS[:]
    if provider == "Together":
        return TG_MODELS[:]
    return OR_MODELS[:] + TG_MODELS[:]


def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any):
    """
    Roteia pelo prefixo do modelo:
      - começa com 'together/'  -> Together
      - caso contrário          -> OpenRouter
    """
    if model.startswith("together/"):
        return together_chat(model, messages, **kwargs)
    return openrouter_chat(model, messages, **kwargs)


# Compat com código legado que envia um payload
def route_chat_strict(model: str, payload: Dict[str, Any]):
    msgs = payload.get("messages", [])
    return chat(
        model,
        msgs,
        max_tokens=payload.get("max_tokens", 1024),
        temperature=payload.get("temperature", 0.7),
        top_p=payload.get("top_p", 0.95),
    )
