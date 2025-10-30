from __future__ import annotations
import os
from typing import Dict, List, Tuple, Any

from .openrouter import chat as openrouter_chat, DEFAULT_MODELS as OR_MODELS
from .together import chat as together_chat, DEFAULT_MODELS as TG_MODELS


# ==============================
# Provedores disponíveis
# ==============================
def available_providers() -> List[Tuple[str, bool, str]]:
    """
    Retorna [(nome, configurado, detalhe)]
    """
    have_or = bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_TOKEN"))
    have_tg = bool(os.getenv("TOGETHER_API_KEY"))
    return [
        ("OpenRouter", have_or, "OK" if have_or else "sem chave"),
        ("Together",   have_tg, "OK" if have_tg else "sem chave"),
    ]


# ==============================
# Lista de modelos
# ==============================
def list_models(provider: str | None = None) -> List[str]:
    if provider == "OpenRouter":
        return OR_MODELS[:]
    if provider == "Together":
        return TG_MODELS[:]
    return OR_MODELS[:] + TG_MODELS[:]


# ==============================
# Roteamento automático por prefixo
# ==============================
def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any):
    """
    Roteia o modelo para o provedor correto:
      - Together: começa com 'together/' OU é 'moonshotai/Kimi-K2-Instruct-0905'
      - OpenRouter: demais modelos
    """
    m = (model or "").lower()

    # Modelos do Together
    if (
        m.startswith("together/")
        or m.startswith("moonshotai/kimi-k2-instruct-0905")
        or m.startswith("google/gemma-3")
        or m.startswith("google/gemini-2.5-flash")
    ):
        return together_chat(model, messages, **kwargs)

    # Demais modelos → OpenRouter
    return openrouter_chat(model, messages, **kwargs)


# ==============================
# Compatibilidade com payload legado
# ==============================
def route_chat_strict(model: str, payload: Dict[str, Any]):
    msgs = payload.get("messages", [])
    return chat(
        model,
        msgs,
        max_tokens=payload.get("max_tokens", 1024),
        temperature=payload.get("temperature", 0.7),
        top_p=payload.get("top_p", 0.95),
    )
