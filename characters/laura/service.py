# characters/laura/service.py
from __future__ import annotations
from typing import List
from core.common.base_service import BaseCharacter
from core.service import gerar_resposta
from core.repositories import get_fact, set_fact

_UKEY = "GLOBAL::laura"  # chave simples para preferências globais

class LauraService(BaseCharacter):
    title = "Laura"

    def render_sidebar(self, sb) -> None:
        sb.subheader("Preferências — Laura")

        virgem = bool(get_fact(_UKEY, "virgem", False))
        nv = sb.checkbox("Virgem", value=virgem)
        if nv != virgem:
            set_fact(_UKEY, "virgem", nv, {"fonte": "sidebar"})

        parceiro = (get_fact(_UKEY, "parceiro_atual", "") or "")
        np = sb.text_input("Parceiro atual", value=parceiro, placeholder="Janio")
        if np != parceiro:
            set_fact(_UKEY, "parceiro_atual", np, {"fonte": "sidebar"})

        boate_locked = bool(get_fact(_UKEY, "arc_boate_locked", False))
        nb = sb.checkbox("Arc: sair da boate (fixo)", value=boate_locked)
        if nb != boate_locked:
            set_fact(_UKEY, "arc_boate_locked", nb, {"fonte": "sidebar"})

        flirt_mode = bool(get_fact(_UKEY, "flirt_mode", False))
        nf = sb.toggle("Permitir quase com terceiros (sem trair)", value=flirt_mode)
        if nf != flirt_mode:
            set_fact(_UKEY, "flirt_mode", nf, {"fonte": "sidebar"})

    def available_models(self) -> List[str]:
        return ["gpt-5"]

    def reply(self, user: str, model: str) -> str:
        return gerar_resposta(usuario="GLOBAL", prompt_usuario=user, model=model, character=self.title)
