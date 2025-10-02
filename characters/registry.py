# characters/registry.py
from __future__ import annotations
from importlib import import_module
from typing import Dict, Tuple, Type, List, Optional, Any, Callable
import inspect

from core.common.base_service import BaseCharacter

# Catálogo: nome visível -> (módulo, classe)
_CATALOG: Dict[str, Tuple[str, str]] = {
    "Mary":   ("characters.mary.service", "MaryService"),
    "Laura":  ("characters.laura.service", "LauraService"),
    "Nerith": ("characters.nerith.service", "NerithService"),
}

def list_characters() -> List[str]:
    return list(_CATALOG.keys())

class _ServiceAdapter(BaseCharacter):
    """Adapta objetos sem interface completa, preenchendo métodos faltantes."""
    def __init__(self, impl: Any, fallback_title: str = "Personagem"):
        self._impl = impl
        self.title = getattr(impl, "title", fallback_title)

    def render_sidebar(self, sb) -> None:
        fn: Optional[Callable] = getattr(self._impl, "render_sidebar", None)
        if callable(fn):
            try:
                fn(sb)
                return
            except Exception:
                pass
        # default
        super().render_sidebar(sb)

    def available_models(self) -> List[str]:
        fn: Optional[Callable] = getattr(self._impl, "available_models", None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, list) and out:
                    return out
            except Exception:
                pass
        return super().available_models()

    def reply(self, user: str, model: str) -> str:
        # 1) se a implementação tiver reply() útil, use-a
        fn: Optional[Callable] = getattr(self._impl, "reply", None)
        if callable(fn):
            try:
                # Evita recair no NotImplemented da BaseCharacter
                if not (inspect.ismethod(fn) and getattr(fn, "__func__", None) is BaseCharacter.reply):
                    return fn(user=user, model=model)
            except Exception:
                # se a implementação própria falhar, cai para o core
                pass
        # 2) fallback genérico: usa core.service.gerar_resposta
        from core.service import gerar_resposta
        character_name = (getattr(self._impl, "title", None) or self.title or "Mary").strip()
        return gerar_resposta(usuario="GLOBAL", prompt_usuario=user, model=model, character=character_name)

def get_service(name: Optional[str]) -> BaseCharacter:
    key = (name or "").strip().title()
    if key not in _CATALOG:
        key = "Mary"
    module_name, cls_name = _CATALOG[key]
    mod = import_module(module_name)
    cls: Type[Any] = getattr(mod, cls_name)
    impl = cls()

    # Se já é BaseCharacter, verifique se reply foi sobrescrito;
    # caso contrário, embrulhe no adapter para cair no fallback.
    if isinstance(impl, BaseCharacter):
        impl_reply = getattr(impl, "reply", None)
        if callable(impl_reply) and not (
            inspect.ismethod(impl_reply) and getattr(impl_reply, "__func__", None) is BaseCharacter.reply
        ):
            return impl

    # Em qualquer outra situação, adaptamos.
    return _ServiceAdapter(impl, fallback_title=key)
