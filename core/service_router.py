# core/service_router.py
from __future__ import annotations

import os
from typing import Dict, List, Tuple, Any

from .openrouter import chat as openrouter_chat, DEFAULT_MODELS as OR_MODELS
from .together import chat as together_chat, DEFAULT_MODELS as TG_MODELS


def available_providers() -> List[Tuple[str, bool, str]]:
    """
    Retorna [(nome, configurado, detalhe)].
    Lista OpenRouter e Together; 'configurado' reflete presença de chave.
    """
    have_or = bool(os.environ.get("OPENROUTER_API_KEY"))
    have_tg = bool(os.environ.get("TOGETHER_API_KEY"))
    return [
        ("OpenRouter", have_or, "OK" if have_or else "sem chave"),
        ("Together",   have_tg, "OK" if have_tg else "sem chave"),
    ]


def list_models(provider: str | None = None) -> List[str]:
    """
    Modelos sugeridos para UI. Se provider=None, concatena ambos.
    """
    if provider == "Together":
        return TG_MODELS[:]
    if provider == "OpenRouter":
        return OR_MODELS[:]
    return OR_MODELS[:] + TG_MODELS[:]


def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any) -> Tuple[Dict[str, Any], str, str]:
    """
    Envia ao provedor conforme o prefixo do modelo:
      - começa com 'together/' → Together
      - senão → OpenRouter
    Retorna (data_json, used_model, provider_tag).
    """
    if model.startswith("together/"):
        return together_chat(model, messages, **kwargs)
    return openrouter_chat(model, messages, **kwargs)


# ---------- Compatibilidade com código legado ----------
def route_chat_strict(model: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """
    Compat: aceita payload no formato antigo:
      {
        "model": "...",
        "messages": [...],
        "max_tokens": int,
        "temperature": float,
        "top_p": float,
      }
    Ignora chaves extras.
    """
    msgs = payload.get("messages", [])
    return chat(
        model,
        msgs,
        max_tokens=payload.get("max_tokens", 1024),
        temperature=payload.get("temperature", 0.7),
        top_p=payload.get("top_p", 0.95),
    )
