# characters/registry.py
from __future__ import annotations
from importlib import import_module
from typing import Dict, Tuple, List, Type
import inspect

from core.common.base_service import BaseCharacter

# Catálogo (sempre chaves minúsculas!)
_CATALOG: Dict[str, Tuple[str, str]] = {
    "mary":   ("characters.mary.service",   "MaryService"),
    "laura":  ("characters.laura.service",  "LauraService"),
    "adelle": ("characters.adelle.service", "AdelleService"),
    "nerith": ("characters.nerith.service", "NerithService"),
}

# Cache por personagem (chave = nome minúsculo)
_SERVICE_CACHE: Dict[str, BaseCharacter] = {}

def clear_service_cache(name: str | None = None) -> None:
    """Limpa o cache de um personagem específico ou todo cache."""
    if name is None:
        _SERVICE_CACHE.clear()
    else:
        _SERVICE_CACHE.pop(name.strip().lower(), None)

def list_characters() -> List[str]:
    # Mostra com a primeira letra maiúscula
    return [k.capitalize() for k in _CATALOG.keys()]

def _load_class(module_name: str, class_name: str) -> Type[BaseCharacter]:
    mod = import_module(module_name)
    cls = getattr(mod, class_name)
    if not inspect.isclass(cls) or not issubclass(cls, BaseCharacter):
        raise TypeError(f"{class_name} não é subclass de BaseCharacter")
    return cls

def _resolve_by_catalog(key: str) -> BaseCharacter:
    module_name, class_name = _CATALOG[key]
    cls = _load_class(module_name, class_name)
    return cls()

def _resolve_by_convention(name_lc: str) -> BaseCharacter:
    # Ex.: characters.laura.service / LauraService
    module_name = f"characters.{name_lc}.service"
    class_name  = f"{name_lc.capitalize()}Service"
    cls = _load_class(module_name, class_name)
    return cls()

def get_service(name: str) -> BaseCharacter:
    """
    Resolve serviço por:
    1) Catálogo (case-insensitive)
    2) Convenção (módulo + classe)
    Cacheia por nome minúsculo.
    """
    key = (name or "").strip().lower()
    if not key:
        key = "mary"

    if key in _SERVICE_CACHE:
        return _SERVICE_CACHE[key]

    inst: BaseCharacter | None = None
    # Tenta catálogo
    if key in _CATALOG:
        try:
            inst = _resolve_by_catalog(key)
        except Exception:
            inst = None

    # Tenta convenção se catálogo falhar/não existir
    if inst is None:
        try:
            inst = _resolve_by_convention(key)
        except Exception:
            inst = None

    # Fallback final seguro: Mary
    if inst is None:
        inst = _resolve_by_catalog("mary")

    _SERVICE_CACHE[key] = inst
    return inst

def list_models_for_character(name: str) -> List[str]:
    """
    (Opcional) Se cada service expõe 'supported_models()', devolve;
    caso contrário retorna lista vazia para a UI não quebrar.
    """
    try:
        svc = get_service(name)
        if hasattr(svc, "supported_models") and callable(getattr(svc, "supported_models")):
            return list(getattr(svc, "supported_models")() or [])
    except Exception:
        pass
    return []
