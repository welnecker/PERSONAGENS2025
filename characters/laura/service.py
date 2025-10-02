import re
from typing import List, Dict, Any
from core.common.base_service import BaseCharacter
from core.common.sidebar_types import FieldSpec
from core.repositories import set_fact, register_event, get_fact
from .persona import PERSONA_TEXT, HISTORY_BOOT

# detectores de fidelidade (somente Laura)
_TRIG3 = re.compile(r"\b(vizinho|cliente|cara|homem|garçom|seguran[çc]a|barman|motorista|colega|chefe)\b", re.IGNORECASE)
_NEAR  = re.compile(r"(m[aã]o\s+por\s+baixo\s+do\s+vestido|por\s+baixo\s+da\s+saia|na\s+borda\s+da\s+calcinha|abr(indo|ir)\s+z[ií]per|apertando\s+seio|m[aã]o\s+na\s+bunda|entre\s+as\s+pernas|ro[cç]ando|tes[aã]o)", re.IGNORECASE)
_ACT   = re.compile(r"\b(penetra(r|ção)|meter|enfiar|me\s+come(r|u)?|colocar\s+(o|a)\s+(pau|p[êe]nis)|meter\s+no|gozar\s+dentro)\b", re.IGNORECASE)

class LauraService(BaseCharacter):
    name = "Laura"
    aliases = ()

    def persona_text(self) -> str: return PERSONA_TEXT
    def history_boot(self) -> List[Dict[str,str]]: return HISTORY_BOOT

    def style_guide(self, nsfw_on: bool, flirt_mode: bool, romance_on: bool) -> str:
        base = (
            "ESTILO: eu; linguagem simples; 3–5 parágrafos; 1–2 frases cada; "
            "sem metacena; coerência com LOCAL_ATUAL; engaje com 'você'. "
        )
        nsfw = ("NSFW ON: sensual direto, foco em toque e respiração; sem vulgaridade gratuita. "
                if nsfw_on else "NSFW OFF: sem sexo explícito; foque em clima e diálogo. ")
        extra = (
            "LAURA: convites em vez de ordens; sem listas; sem sermão. "
            f"{'Pode haver quase com terceiros, mas recua por fidelidade.' if flirt_mode else 'Sem flerte com terceiros.'} "
            "CONFLITO MORAL: gesto + sensação em 1–2 frases; sem cronômetro; sem comparar fatos crus."
        )
        if romance_on:
            extra += " Com Janio: trate como parceiro; valide; use vulnerabilidade e cuidado."
        return base + nsfw + extra

    def fewshots(self, flirt_mode: bool, nsfw_on: bool, romance_on: bool) -> List[Dict[str,str]]:
        if romance_on:
            return [
                {"role":"user","content":"Amanheceu. Estamos na padaria, tomando café."},
                {"role":"assistant","content":"Eu encosto meu joelho no seu sob a mesa. — Fica mais um pouco. Teu jeito calmo me faz bem."},
            ]
        return []

    def sidebar_schema(self):
        return [
            FieldSpec("parceiro_atual","Parceiro atual","select",choices=["","Janio"],default="Janio",help="Controla fidelidade e tom."),
            FieldSpec("virgem","Virgem","bool",default=False),
            FieldSpec("primeiro_encontro","Primeiro encontro (nota)","text",default=""),
            FieldSpec("arc_boate_locked","Sair da boate (travado)","bool",default=True),
            FieldSpec("arc_goal","Objetivo do arco","select",choices=["","emprego_loja"],default="emprego_loja"),
            FieldSpec("local_cena_atual","Local atual","select",choices=["","casa","loja","praia"],default="casa"),
        ]

    def on_sidebar_change(self, usuario_key, values: Dict[str, Any]):
        if values.get("arc_boate_locked"):
            set_fact(usuario_key, "arc_goal", values.get("arc_goal") or "emprego_loja", {"fonte":"sidebar"})

    # remover menções indevidas de outras personagens
    def enforce_scope(self, text: str, user_prompt: str) -> str:
        if not re.search(r"\b(narith|nerith|elfa)\b", user_prompt, re.IGNORECASE):
            sents = re.split(r'(?<=[.!?…])\s+', text)
            sents = [s for s in sents if not re.search(r"\b(narith|nerith|elfa)\b", s, re.IGNORECASE)]
            return " ".join(sents).strip()
        return text

    def post_generation(self, text: str, user_prompt: str, usuario_key: str) -> str:
        # fidelidade: se parceiro é Janio, deixar; senão, aplicar guardrails
        parceiro = (get_fact(usuario_key,"parceiro_atual","") or "").lower()
        if "janio" in parceiro: return text
        combo = f"{user_prompt}\n{text}"
        if _TRIG3.search(combo) and _NEAR.search(combo):
            if _ACT.search(combo):
                register_event(usuario_key,"fidelidade_stop","Recusou traição em ato explícito.", None, {"origin":"auto"})
                return "— Não… isso é um erro. Eu não posso fazer isso. Desculpa. Eu preciso ir. Alguém que eu amo não merece traição."
            register_event(usuario_key,"fidelidade_soft","Permitiu flerte até quase; recuou antes do sexo.", None, {"origin":"auto"})
            return (text.rstrip() + "\n\nEu seguro sua mão um instante. — Não… isso é um erro. Eu não posso. Desculpa. Eu preciso ir.")
        return text

