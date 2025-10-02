from typing import List, Dict
from core.common.base_service import BaseCharacter
from core.common.sidebar_types import FieldSpec
from .persona import PERSONA_TEXT, HISTORY_BOOT

class MaryService(BaseCharacter):
    name = "Mary"
    aliases = ()

    def persona_text(self) -> str: return PERSONA_TEXT
    def history_boot(self) -> List[Dict[str,str]]: return HISTORY_BOOT

    def style_guide(self, nsfw_on: bool, flirt_mode: bool, romance_on: bool) -> str:
        base = "ESTILO: eu; 3–5 parágrafos; 1–2 frases cada; flerte com humor; sem metacena."
        nsfw = " NSFW ON: sensual leve e consentida; sem vulgaridade." if nsfw_on else " NSFW OFF."
        return base + nsfw

    def sidebar_schema(self):
        return [
            FieldSpec("flirt_mode","Modo flerte","bool",default=False),
            FieldSpec("local_cena_atual","Local atual","select",choices=["","casa","praia","loja"], default="casa"),
        ]

