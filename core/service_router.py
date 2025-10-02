# core/service_router.py
from __future__ import annotations
import os
from typing import Dict, Any, Tuple
from .openrouter import chat as openrouter_chat
from .together import chat as together_chat
from .models import list_models as _list_models, available_providers as _available_providers

def available_providers():
    return _available_providers()

def list_models(provider: str):
    return _list_models(provider)

def _guess_provider(model: str) -> str:
    m = (model or "").lower()
    # heurística básica
    if m.startswith(("anthropic/", "openai/", "google/", "mistral/", "openrouter/")):
        return "openrouter" if os.getenv("OPENROUTER_API_KEY") else "together"
    # muitos ids do Together começam com org/model
    return "together" if os.getenv("TOGETHER_API_KEY") else "openrouter"

def route_chat_strict(model: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """
    Roteia para o provedor correto. Se payload['provider'] vier, respeita.
    Caso contrário, tenta deduzir pelo nome do modelo e chaves disponíveis.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload inválido")
    payload = dict(payload)  # cópia defensiva
    payload["model"] = model

    provider = (payload.get("provider") or "").lower().strip()
    if not provider:
        provider = _guess_provider(model)

    if provider == "openrouter":
        return openrouter_chat(payload)
    if provider == "together":
        return together_chat(payload)

    raise ValueError(f"Provider inválido: {provider}")
