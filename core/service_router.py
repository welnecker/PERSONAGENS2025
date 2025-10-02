# core/service_router.py
from __future__ import annotations

import os
from typing import Dict, List, Tuple, Any

# Imports dos backends (OpenRouter / Together).
# Estes módulos devem expor: chat(model, messages, **kwargs) -> (data, used_model, "openrouter"/"together")
# e DEFAULT_MODELS: List[str]
try:
    from .openrouter import chat as openrouter_chat, DEFAULT_MODELS as OR_MODELS  # type: ignore
except Exception:
    openrouter_chat = None
    OR_MODELS = [
        # fallback de catálogo mínimo
        "deepseek/deepseek-chat-v3-0324",
        "anthropic/claude-3.5-haiku",
        "qwen/qwen3-max",
        "nousresearch/hermes-3-llama-3.1-405b",
    ]

try:
    from .together import chat as together_chat, DEFAULT_MODELS as TG_MODELS  # type: ignore
except Exception:
    together_chat = None
    TG_MODELS = [
        # fallback de catálogo mínimo
        "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        "together/Qwen/Qwen2.5-72B-Instruct",
        "together/Qwen/QwQ-32B",
    ]


def _has_or_key() -> bool:
    # aceita OPENROUTER_API_KEY ou OPENROUTER_TOKEN (compat)
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_TOKEN"))


def _has_tg_key() -> bool:
    return bool(os.environ.get("TOGETHER_API_KEY"))


def available_providers() -> List[Tuple[str, bool, str]]:
    """
    Retorna [(nome, configurado, detalhe)].
    """
    have_or = _has_or_key()
    have_tg = _has_tg_key()
    return [
        ("OpenRouter", have_or, "OK" if have_or else "sem chave"),
        ("Together",   have_tg, "OK" if have_tg else "sem chave"),
    ]


def list_models(provider: str | None = None) -> List[str]:
    """
    Modelos sugeridos para UI. Se provider=None, concatena ambos (OpenRouter primeiro).
    """
    if provider == "OpenRouter":
        return OR_MODELS[:]
    if provider == "Together":
        return TG_MODELS[:]
    # sem filtro → todos
    return OR_MODELS[:] + TG_MODELS[:]


def _choose_backend(model: str):
    """
    Decide provedor pelo prefixo do modelo:
      - começa com 'together/' → Together
      - caso contrário → OpenRouter
    Retorna (provider_name, callable_chat).
    """
    if model.startswith("together/"):
        return "together", together_chat
    return "openrouter", openrouter_chat


def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any) -> Tuple[Dict[str, Any], str, str]:
    """
    Envia ao provedor selecionado e retorna (data_json, used_model, provider_tag).
    'kwargs' aceita: max_tokens, temperature, top_p, etc.
    Erros são transformados em RuntimeError com mensagem legível.
    """
    provider, fn = _choose_backend(model)

    # Valida chaves antes de chamar HTTP
    if provider == "openrouter":
        if not _has_or_key():
            raise RuntimeError(
                "OpenRouter sem chave. Defina OPENROUTER_API_KEY (ou OPENROUTER_TOKEN) em st.secrets ou env."
            )
        if fn is None:
            raise RuntimeError("Backend OpenRouter não disponível (módulo core/openrouter.py ausente ou com erro).")
    else:  # together
        if not _has_tg_key():
            raise RuntimeError("Together sem chave. Defina TOGETHER_API_KEY em st.secrets ou env.")
        if fn is None:
            raise RuntimeError("Backend Together não disponível (módulo core/together.py ausente ou com erro).")

    # Normaliza kwargs principais
    max_tokens = int(kwargs.get("max_tokens", 1024))
    temperature = float(kwargs.get("temperature", 0.7))
    top_p = float(kwargs.get("top_p", 0.95))

    try:
        data, used_model, prov = fn(  # type: ignore[misc]
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        return data, used_model, prov
    except Exception as e:
        # Repasse com mensagem clara para a UI
        raise RuntimeError(f"Falha no provedor {provider}: {e}") from e


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
    Ignora chaves extras. Seleciona provedor conforme o 'model'.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload deve ser dict com chaves: model, messages, max_tokens, temperature, top_p.")

    msgs = payload.get("messages") or []
    if not isinstance(msgs, list) or (not msgs):
        # ainda permitimos lista vazia, mas isso geralmente é erro de chamada
        msgs = msgs if isinstance(msgs, list) else []

    return chat(
        model=model,
        messages=msgs,
        max_tokens=payload.get("max_tokens", 1024),
        temperature=payload.get("temperature", 0.7),
        top_p=payload.get("top_p", 0.95),
    )
