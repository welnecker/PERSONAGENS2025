from typing import Dict, Type
from core.common.base_service import BaseCharacter
from characters.laura.service import LauraService
from characters.nerith.service import NerithService
from characters.mary.service import MaryService

_SERVICES: Dict[str, BaseCharacter] = {
    "laura": LauraService(),
    "nerith": NerithService(),
    "narith": NerithService(),
    "elfa": NerithService(),
    "mary": MaryService(),
}

def get_service(name: str) -> BaseCharacter:
    key = (name or "mary").strip().lower()
    return _SERVICES.get(key, _SERVICES["mary"])

