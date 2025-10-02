# characters/nerith/service.py
from __future__ import annotations
from typing import List
from core.common.base_service import BaseCharacter
from core.service import gerar_resposta
from core.repositories import get_fact, set_fact

_UKEY = "GLOBAL::nerith"

class NerithService(BaseCharacter):
    title = "Nerith"

    def render_sidebar(self, sb) -> None:
        sb.subheader("Preferências — Nerith")
        # Exemplo de ajuste leve, sem “lore” barulhento: só flags úteis
        intensidade = int(get_fact(_UKEY, "intensidade", 2))  # 1 suave, 2 médio, 3 intenso
        ni = sb.slider("Intensidade sensorial", min_value=1, max_value=3, value=intensidade)
        if ni != intensidade:
            set_fact(_UKEY, "intensidade", ni, {"fonte": "sidebar"})

    def available_models(self) -> List[str]:
        return ["gpt-5"]

    def reply(self, user: str, model: str) -> str:
        return gerar_resposta(usuario="GLOBAL", prompt_usuario=user, model=model, character=self.title)
