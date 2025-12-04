# core/nsfw.py
from __future__ import annotations
from typing import Optional
import re
from .repositories import get_fact

# --- padrões SEGUROS (sem \c) ---
_PRIV_LOC_PATTERNS = [
    r"\b(apartament\w+|apto\b|kitnet|loft|casa|sobrado|ph\b|penthous\w*|su[ií]te|hotel|motel|chal[eé]|airbnb|pousada|cabana)\b",
]
_SECLUDED_BEACH_PATTERNS = [
    r"\b(praia)\b.*\b(desert[ao]|isolad[ao]|vazi[ao])\b",
    r"\b(desert[ao]|isolad[ao]|vazi[ao])\b.*\b(praia)\b",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    t = (text or "").lower()
    # blindagem contra escapes ruins vindos do texto
    t = t.replace("\\", "\\\\")
    return any(re.search(p, t) for p in patterns)


def is_private_location(local_atual: Optional[str]) -> bool:
    if not local_atual:
        return False
    t = (local_atual or "").lower()
    if _matches_any(t, _PRIV_LOC_PATTERNS):
        return True
    if _matches_any(t, _SECLUDED_BEACH_PATTERNS):
        return True
    return False


def nsfw_enabled(usuario: str, local_atual: Optional[str] = None) -> bool:
    """
    Gate NSFW simplificado:

      - Se nsfw_override == "off"  -> BLOQUEIA
      - Qualquer outro caso        -> LIBERA (default ON)
    """
    override = (get_fact(usuario, "nsfw_override", "") or "").lower()

    # Botão do sidebar ainda pode bloquear
    if override == "off":
        return False

    # Fora isso, sempre liberado
    return True

