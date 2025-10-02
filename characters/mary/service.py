# characters/mary/service.py
from __future__ import annotations
from typing import List
from core.common.base_service import BaseCharacter
from core.service import gerar_resposta

class MaryService(BaseCharacter):
    title = "Mary"

    # Sidebar opcional (padrão no-op da base já serve)
    def available_models(self) -> List[str]:
        return ["gpt-5"]

    def reply(self, user: str, model: str) -> str:
        # Chama o core genérico com o nome da personagem
        return gerar_resposta(usuario="GLOBAL", prompt_usuario=user, model=model, character=self.title)

