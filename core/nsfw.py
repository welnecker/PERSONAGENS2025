# core/nsfw.py
from __future__ import annotations
from typing import Optional
import re
from .repositories import get_fact

# (mantém os padrões se você ainda quiser usar em outro lugar,
# mas o gate principal vai ser SÓ o override)

def nsfw_enabled(usuario: str, local_atual: Optional[str] = None) -> bool:
    """
    Gate NSFW SIMPLIFICADO:
      - Se nsfw_override == 'on'  -> True
      - Se nsfw_override == 'off' -> False
      - Se não tiver override, padrão = True (liberado)
    """
    override = (get_fact(usuario, "nsfw_override", "") or "").lower()
    if override == "on":
        return True
    if override == "off":
        return False

    # Padrão: liberado
    return True
