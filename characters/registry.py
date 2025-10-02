# characters/registry.py
from __future__ import annotations
from importlib import import_module
from typing import Dict, Tuple, Type, List, Optional, Any, Callable

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
            fn(sb)
        else:
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
        fn: Optional[Callable] = getattr(self._impl, "reply", None)
        if not callable(fn):
            return super().reply(user, model)  # levanta NotImplementedError
        return fn(user=user, model=model)

def get_service(name: Optional[str]) -> BaseCharacter:
    key = (name or "").strip().title()
    if key not in _CATALOG:
        key = "Mary"
    module_name, cls_name = _CATALOG[key]
    mod = import_module(module_name)
    cls: Type[Any] = getattr(mod, cls_name)
    impl = cls()  # pode ser um objeto sem todos os métodos
    # Se já herda BaseCharacter e tem tudo, retorna direto; senão, adapta.
    if isinstance(impl, BaseCharacter) and all(
        callable(getattr(impl, m, None)) for m in ("render_sidebar", "available_models", "reply")
    ):
        return impl
    return _ServiceAdapter(impl, fallback_title=key)


