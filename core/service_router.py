# core/service_router.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Optional

# ======================================================================
# Listagem de provedores e modelos
# ======================================================================

def available_providers() -> List[str]:
    """Retorna provedores habilitados com base nas variáveis de ambiente."""
    provs: List[str] = []
    if os.getenv("OPENROUTER_API_KEY"):
        provs.append("openrouter")
    if os.getenv("TOGETHER_API_KEY"):
        provs.append("together")
    # fallback amigável se nenhum estiver configurado
    return provs or ["openrouter", "together"]

def _parse_models_env(var_value: Optional[str], defaults: List[str]) -> List[str]:
    if not var_value:
        return defaults
    raw = [x.strip() for x in var_value.split(",")]
    return [x for x in raw if x]

def list_models(provider: str) -> List[str]:
    """Modelos por provedor (via ENV ou defaults razoáveis)."""
    provider = (provider or "").strip().lower()
    if provider == "openrouter":
        # Pode definir OPENROUTER_MODELS="openrouter/whatever,anthropic/claude-3.5,openai/gpt-4o..."
        return _parse_models_env(
            os.getenv("OPENROUTER_MODELS"),
            [
                "openrouter/auto",
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
                "anthropic/claude-3.5-sonnet",
            ],
        )
    if provider == "together":
        return _parse_models_env(
            os.getenv("TOGETHER_MODELS"),
            [
                "meta-llama/Meta-Llama-3-70B-Instruct-Turbo",
                "google/gemma-2-27b-it",
            ],
        )
    return []

# ======================================================================
# Roteamento de chamadas (imports preguiçosos)
# ======================================================================

def route_chat(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Roteia para o provedor correto. Retorna (data, used_model, provider_name).
    """
    provider = (provider or "").strip().lower()
    if provider == "openrouter":
        from .openrouter import chat as openrouter_chat  # import lazy
        return openrouter_chat(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            extra_params=extra_params,
        )
    if provider == "together":
        from .together import chat as together_chat  # import lazy
        return together_chat(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            extra_params=extra_params,
        )
    raise RuntimeError(f"Provedor desconhecido: {provider}")

# ----------------------------------------------------------------------
# Compatibilidade retro (assinatura antiga usada em alguns serviços)
# ----------------------------------------------------------------------
def route_chat_strict(model: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """
    Compat: aceita `payload={"model":..., "messages":[...], "max_tokens":..., "temperature":..., "top_p":..., "provider":...}`
    Se `provider` não vier, tenta escolher um automaticamente.
    """
    prov = (payload.get("provider") or "").strip().lower()
    if not prov:
        # tenta inferir provedor por ENV
        if os.getenv("OPENROUTER_API_KEY"):
            prov = "openrouter"
        elif os.getenv("TOGETHER_API_KEY"):
            prov = "together"
        else:
            raise RuntimeError(
                "Nenhum provedor configurado. Defina OPENROUTER_API_KEY ou TOGETHER_API_KEY."
            )
    return route_chat(
        provider=prov,
        model=payload.get("model") or model,
        messages=payload.get("messages") or [],
        max_tokens=int(payload.get("max_tokens", 2048)),
        temperature=float(payload.get("temperature", 0.7)),
        top_p=float(payload.get("top_p", 0.9)),
        extra_params=payload.get("extra_params"),
    )
