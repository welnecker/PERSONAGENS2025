# characters/registry.py
from __future__ import annotations
from importlib import import_module
from typing import Dict, Tuple, Type, List, Optional

from core.common.base_service import BaseCharacter

# Catálogo: nome visível -> (módulo, classe)
# Deixe somente referências de string; NUNCA importe services no topo para evitar ImportError cedo.
_CATALOG: Dict[str, Tuple[str, str]] = {
    "Mary":   ("characters.mary.service", "MaryService"),
    "Laura":  ("characters.laura.service", "LauraService"),
    "Nerith": ("characters.nerith.service", "NerithService"),  # ou "Narith" se preferir
}

def list_characters() -> List[str]:
    return list(_CATALOG.keys())

def get_service(name: Optional[str]) -> BaseCharacter:
    key = (name or "").strip().title()
    if key not in _CATALOG:
        key = "Mary"
    module_name, cls_name = _CATALOG[key]
    mod = import_module(module_name)
    cls: Type[BaseCharacter] = getattr(mod, cls_name)
    return cls()


