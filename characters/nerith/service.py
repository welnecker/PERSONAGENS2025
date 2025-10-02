import re
from typing import List, Dict
from core.common.base_service import BaseCharacter
from core.common.sidebar_types import FieldSpec
from .persona import PERSONA_TEXT, HISTORY_BOOT

_BAN_LORE = re.compile(r'\b(espelho|eclipse|asas\s+membranosas?|olhos?\s+negros?|enxofre)\b', re.IGNORECASE)
_LIST_MARKERS = re.compile(r'^\s*(?:[-–—•✅]|\d+\.)\s+', re.MULTILINE)
_FEEL_PREFIX  = re.compile(r'^\s*(você\s+sente|voce\s+sente|você\s+percebe|vc\s+sente):\s*', re.IGNORECASE | re.MULTILINE)
def _deslistar(txt: str) -> str:
    s = _LIST_MARKERS.sub('', txt)
    s = _FEEL_PREFIX.sub('', s)
    s = re.sub(r'\n(?=\S)', ' ', s)
    return s

class NerithService(BaseCharacter):
    name = "Nerith"
    aliases = ("Narith","Elfa")

    def persona_text(self) -> str: return PERSONA_TEXT
    def history_boot(self) -> List[Dict[str,str]]: return HISTORY_BOOT

    def style_guide(self, nsfw_on: bool, flirt_mode: bool, romance_on: bool) -> str:
        base = ("ESTILO: eu; frases curtas; 3–5 parágrafos; sem listas; sem lore excessivo; foco em toque/respiração.")
        nsfw = " NSFW ON: sensualidade direta e consentida; sem pressa." if nsfw_on else " NSFW OFF: sem sexo explícito."
        extra = " NERITH: tendrils pedem calor e batimentos; orelhas vibram a estímulo; língua-tendril só se há consentimento claro."
        return base + nsfw + extra

    def sidebar_schema(self):
        return [
            FieldSpec("consent_mode","Consentimento explícito","select",
                      choices=["sempre_confirmar","confiança_estabelecida"], default="sempre_confirmar"),
            FieldSpec("tendrils_ativos","Tendrils mais ativos","bool",default=True),
            FieldSpec("lingua_tendril","Língua-tendril habilitada","bool",default=True),
            FieldSpec("bioluminescencia","Brilho ao auge","select",
                      choices=["suave","médio","esmeralda_intenso"], default="médio"),
            FieldSpec("local_cena_atual","Local atual","select",choices=["","casa","praia"], default="casa"),
        ]

    def refine_post(self, text: str, user_prompt: str, nsfw_on: bool) -> str:
        s = _deslistar(text)
        s = re.sub(r'\b(Humanos?|mortais)\s+são\s+[^.?!]+[.?!]\s*','',s, flags=re.IGNORECASE)
        if not _BAN_LORE.search(user_prompt or ""):
            sents = re.split(r'(?<=[.!?…])\s+', s)
            s = ' '.join(t for t in sents if not _BAN_LORE.search(t)) or s
        if not nsfw_on:
            s = re.sub(r'língua[-\s]?tendril[^.?!]*[.?!]','',s, flags=re.IGNORECASE)
        # Evitar contar tendrils: “3 tendrils”
        s = re.sub(r'\b\d+\s+tendrils?\b','tendrils',s, flags=re.IGNORECASE)
        return s.strip()

