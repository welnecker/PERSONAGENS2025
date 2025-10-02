# core/common/base_service.py
from __future__ import annotations
from typing import List

class BaseCharacter:
    """Base concreta com defaults seguros (no-op)."""

    title: str = "Personagem"

    def render_sidebar(self, sb) -> None:
        """Default: sem opções específicas."""
        try:
            sb.caption("Sem preferências para esta personagem.")
        except Exception:
            # Em ambientes sem UI (ex.: testes), apenas ignore
            pass

    def available_models(self) -> List[str]:
        """Default: retorna uma lista de modelos padrão."""
        return ["gpt-5"]

    def reply(self, user: str, model: str) -> str:
        """Obrigatório sobrescrever nas personagens."""
        raise NotImplementedError("reply() não implementado para esta personagem.")


