# characters/nerith/service.py
from __future__ import annotations

from typing import List, Dict

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import save_interaction, get_history_docs
from core.tokens import toklen

try:
    # Persona específica da Nerith/Narith
    from .persona import get_persona  # -> (persona_text: str, history_boot: List[Dict[str, str]])
except Exception:
    # Fallback simples
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é NERITH (Narith). Fale em primeira pessoa. Sensual direta, sem listas. "
            "Pele azul, orelhas pontudas que vibram ao estímulo; tendrils buscam calor; "
            "descrição sensorial com consentimento; 1–2 frases por parágrafo; 3–5 parágrafos."
        )
        return txt, []


class NerithService(BaseCharacter):
    title = "Nerith"

    # Deixe None para usar a lista global do app
    def available_models(self):
        return None

    def render_sidebar(self, container) -> None:
        container.markdown("**Nerith** — sensualidade direta, sem listas; consentimento sempre.")

    # Assinatura compatível com main.py
    def reply(self, user: str, model: str, prompt: str) -> str:
        prompt = (prompt or "").strip()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::nerith"

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": persona_text}]
            + self._montar_historico(usuario_key, history_boot)
            + [{"role": "user", "content": prompt}]
        )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.6,
            "top_p": 0.9,
        }

        data, used_model, provider = route_chat_strict(model, payload)
        texto = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

        # Fallback suave caso venha vazio
        if not texto.strip():
            payload2 = {**payload, "temperature": 0.4}
            data2, used_model, provider = route_chat_strict(model, payload2)
            texto = (data2.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

        # Persistência
        save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        return texto

    # ===== utilidades internas =====
    def _load_persona(self) -> (str, List[Dict[str, str]]):
        return get_persona()

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        limite_tokens: int = 120_000
    ) -> List[Dict[str, str]]:
        docs = get_history_docs(usuario_key)
        if not docs:
            return history_boot[:]
        total = 0
        out: List[Dict[str, str]] = []
        for d in reversed(docs):
            u = d.get("mensagem_usuario") or ""
            # Mantemos o mesmo campo padrão do repositório para respostas
            a = d.get("resposta_mary") or ""
            t = toklen(u) + toklen(a)
            if total + t > limite_tokens:
                break
            out.append({"role": "user", "content": u})
            out.append({"role": "assistant", "content": a})
            total += t
        return list(reversed(out)) if out else history_boot[:]
