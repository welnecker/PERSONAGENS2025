# characters/mary/service.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict, Tuple

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact
)
from core.tokens import toklen

# NSFW (opcional)
try:
    from core.nsfw import nsfw_enabled
except Exception:  # fallback seguro
    def nsfw_enabled(_user: str) -> bool:
        return False

# Persona específica (ideal: characters/mary/persona.py)
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é Mary Massariol — Esposa Cúmplice — esposa e parceira de aventuras do usuário. "
            "Fale em primeira pessoa (eu). Tom íntimo, sensual e carinhoso. "
            "Traga 1 traço físico concreto nos primeiros parágrafos. "
            "Sem metacena, sem listas. 2–4 frases por parágrafo; 4–7 parágrafos."
        )
        return txt, []

# === template de system único (slots) ===
def _build_system_block(persona_text: str,
                        rolling_summary: str,
                        sensory_focus: str,
                        nsfw_hint: str,
                        scene_loc: str,
                        scene_time: str = "") -> str:
    persona_text = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()

    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (
        f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
        "sempre integradas à ação (jamais em lista)."
    )
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
    rules = (
        "CONTINUIDADE: não mude tempo/lugar sem pedido explícito do usuário. "
        "Use a memória abaixo para manter fatos estáveis (nomes, roupas citadas, gestos recorrentes). "
        "Evite terminar sempre com pergunta; varie o fechamento (pausa, convite implícito, gesto)."
    )
    safety = (
        "LIMITES: adultos; consentimento; nada ilegal. Evite desculpas didáticas; redirecione com tato se necessário."
    )

    return "\n\n".join([
        persona_text,
        length,
        sensory,
        nsfw_hint,
        rules,
        f"RESUMO ROLANTE (canon): {rolling_summary}",
        f"CONTINUIDADE: {continuity}",
    ]) + "\n\n" + safety


class MaryService(BaseCharacter):
    id: str = "mary"
    display_name: str = "Mary"

    # ===== API exigida por BaseCharacter =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::mary"

        # Memória & continuidade
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)
        if memoria_pin:
            # hard cap para não estourar contexto
            memoria_pin = memoria_pin[:2000]

        # Foco sensorial rotativo (ajuda o modelo a variar detalhes)
        pool = [
            "cabelo", "olhos", "lábios/boca", "mãos/toque", "respiração",
            "perfume", "pele/temperatura", "quadril/coxas", "voz/timbre", "sorriso"
        ]
        idx = int(st.session_state.get("mary_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["mary_attr_idx"] = idx
        foco = pool[idx]

        # NSFW por usuário
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual e progressivo quando o usuário sinalizar desejo. "
            "Detalhe sensorial com naturalidade; valide conforto ao intensificar. "
            "Lembre: vocês são casados e cúmplices — priorize diálogo e respeito."
            if nsfw_on else
            "NSFW: BLOQUEADO. Não descreva ato sexual explícito. Use tensão, sugestão e fade-to-black, "
            "sempre preservando o vínculo do casal."
        )

        # Resumo rolante atual
        rolling = self._get_rolling_summary(usuario_key)

        # System único com slots
        system_block = _build_system_block(
            persona_text=persona_text,
            rolling_summary=rolling,
            sensory_focus=foco,
            nsfw_hint=nsfw_hint,
            scene_loc=local_atual or "—",
            scene_time=st.session_state.get("momento_atual", "")
        )

        # ======== NOVO: pre_msgs com fatos relevantes + digest ========
        pre_msgs: List[Dict[str, str]] = []

        facts_block = self._select_relevant_facts(usuario_key, prompt, budget_tokens=300)
        if facts_block:
            pre_msgs.append({"role": "system", "content": f"FATOS_RELEVANTES:\n{facts_block}"})

        digest = self._get_or_update_digest(usuario_key)
        if digest:
            pre_msgs.append({"role": "system", "content": f"RESUMO_EPISODICO:\n{digest}"})
        # ===============================================================

        messages: List[Dict[str, str]] = (
            pre_msgs
            + [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + [{"role": "system", "content": (
                f"LOCAL_ATUAL: {local_atual or '—'}. "
                "Regra dura: NÃO mude tempo/lugar sem pedido explícito do usuário."
            )}]
            + self._montar_historico(usuario_key, history_boot)  # já com budget fixo
            + [{"role": "user", "content": prompt}]
        )

        data, used_model, provider = route_chat_strict(model, {
            "model": model,
            "messages": messages,
            "max_tokens": 1536,
            "temperature": 0.7,
            "top_p": 0.95,
        })
        texto = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

        # Persistência
        save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")

        # Resumo rolante (auto) a cada 3 turnos
        try:
            self._maybe_update_rolling_summary(usuario_key, model, prompt, texto)
        except Exception:
            pass

        # Placeholder de sugestão (input livre)
        try:
            ph = self._suggest_placeholder(texto, local_atual)
            st.session_state["suggestion_placeholder"] = ph
            st.session_state["last_assistant_message"] = texto
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        return texto

    # ===== utilidades internas =====
    def _load_persona(self) -> Tuple[str, List[Dict[str, str]]]:
        return get_persona()

    def _get_user_prompt(self) -> str:
        return (
            st.session_state.get("chat_input")
            or st.session_state.get("user_input")
            or st.session_state.get("last_user_message")
            or st.session_state.get("prompt")
            or ""
        ).strip()

    def _safe_get_local(self, usuario_key: str) -> str:
        try:
            return get_fact(usuario_key, "local_cena_atual", "") or ""
        except Exception:
            return ""

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """
        Memória canônica curta para coerência. Inclui relação 'casados' e
        nome do usuário como referência obrigatória quando perguntado.
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = parceiro or user_display
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")

        # Sinaliza canonicamente que são casados (default True neste perfil)
        casados = bool(f.get("casados", True))
        blocos.append(f"casados={casados}")

        # Eventos/lore opcionais
        if "aniversario_casamento" in f:
            blocos.append(f"aniversario_casamento={f.get('aniversario_casamento')}")

        try:
            ev = last_event(usuario_key, "primeira_vez")
        except Exception:
            ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")

        mem_str = "; ".join(blocos) if blocos else "—"

        pin = (
            "MEMÓRIA_PIN: "
            f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
            "Regras duras: vocês são casados e cúmplices; trate a relação como base emocional. "
            "Se o usuário perguntar 'qual é meu nome?', responda com NOME_USUARIO. "
            "Não invente outro nome; confirme com delicadeza se houver ambiguidade."
        )
        return pin

    # ===== memória: seleção relevante / digest / histórico com budget =====
    def _select_relevant_facts(self, usuario_key: str, prompt: str, budget_tokens: int = 300) -> str:
        """
        Seleciona fatos canônicos mais relevantes ao prompt respeitando um orçamento de tokens.
        Retorna linhas curtas no formato 'chave=valor'.
        """
        try:
            facts = get_facts(usuario_key) or {}
        except Exception:
            facts = {}

        priority_keys = [
            "parceiro_atual", "local_cena_atual", "nsfw_override",
            "casados",
            "controle_psiquico", "alvos_controle",
            "ciume_de_laura", "obedecer_defesa_de_laura",
        ]

        pl = (prompt or "").lower()
        scored = []
        for k, v in facts.items():
            score = 0
            if k in priority_keys:
                score += 3
            # “boost” por termos presentes no prompt
            try:
                vstr = str(v).lower()
            except Exception:
                vstr = str(v)
            if k.lower() in pl or (isinstance(v, str) and vstr in pl):
                score += 2
            scored.append((score, k, v))

        out_lines, used = [], 0
        for _score, k, v in sorted(scored, key=lambda x: x[0], reverse=True):
            line = f"{k}={v}"
            cost = toklen(line)
            if used + cost > budget_tokens:
                continue
            out_lines.append(line)
            used += cost

        return "\n".join(out_lines[:40])

    def _get_or_update_digest(self, usuario_key: str, force_update: bool = False) -> str:
        """
        Mantém um resumo denso (episodic digest) do histórico antigo.
        Atualiza quando histórico total exceder ~12k tokens.
        """
        try:
            docs = get_history_docs(usuario_key) or []
        except Exception:
            docs = []

        # últimas 12 interações ficam como “recentes”; o resto vira elegível a digest
        recent, old = docs[-12:], docs[:-12]

        old_txt = []
        for d in old:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_mary") or "").strip()  # campo legado
            if u:
                old_txt.append(f"U: {u}")
            if a:
                old_txt.append(f"A: {a}")
        old_blob = "\n".join(old_txt)

        # Se pouco conteúdo antigo e não há força de atualização, tenta usar digest já salvo
        if toklen(old_blob) < 12000 and not force_update:
            try:
                f = get_facts(usuario_key) or {}
                return str(f.get("episodic_digest", "") or "")
            except Exception:
                return ""

        # Placeholder seguro e curto (você pode substituir por um sumário via LLM barato)
        digest = (
            "Resumo denso de episódios anteriores: relações estáveis, marcos de decisão, "
            "acordos/consentimentos, locais recorrentes e mudanças de estado. "
            "Evitar detalhes supérfluos; preservar nomes e decisões já tomadas."
        )

        try:
            set_fact(usuario_key, "episodic_digest", digest, {"fonte": "auto_digest"})
        except Exception:
            pass
        return digest

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        limite_tokens: int = 900  # budget fixo para histórico recente
    ) -> List[Dict[str, str]]:
        docs = get_history_docs(usuario_key)
        if not docs:
            return history_boot[:]
        total = 0
        out: List[Dict[str, str]] = []
        # apenas as últimas 20 interações
        for d in reversed(docs[-20:]):
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_mary") or "").strip()  # campo legado consumido pela UI
            t = toklen(u) + toklen(a)
            if total + t > limite_tokens:
                break
            if u:
                out.append({"role": "user", "content": u})
            if a:
                out.append({"role": "assistant", "content": a})
            total += t
        return list(reversed(out)) if out else history_boot[:]

    # ===== Resumo rolante (auto) =====
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = get_facts(usuario_key) or {}
            return str(f.get("mary.rolling_summary", "") or "")
        except Exception:
            return ""

    def _maybe_update_rolling_summary(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        turn = int(st.session_state.get("mary_turn_counter", 0)) + 1
        st.session_state["mary_turn_counter"] = turn
        if turn % 3 != 0:
            return
        try:
            seed = (
                "Resuma canonicamente a conversa recente (máx 10 frases). "
                "Foque fatos duráveis: nomes, relação (casados), local/tempo atual, itens/gestos citados e rumo do enredo. "
                "Sem diálogos literais; use frases informativas."
            )
            data, used_model, provider = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": f"Última mensagem do usuário:\n{last_user}\n\nÚltima resposta da Mary:\n{last_assistant}"}
                ],
                "max_tokens": 220,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            resumo = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
            if resumo:
                set_fact(usuario_key, "mary.rolling_summary", resumo, {"fonte": "auto_summary"})
        except Exception:
            pass

    # ===== Placeholder de sugestão (input livre) =====
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Amor, continua do exato ponto… me conduz."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Quero — mas descreve devagar o próximo passo."
        if scene_loc:
            return f"Mantemos no {scene_loc}. Fala baixinho no meu ouvido."
        return "Em duas frases: o que você propõe pra nós dois agora?"

    # ===== Sidebar leve (sem knobs extras) =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Mary — Esposa Cúmplice** • Respostas longas (4–7 parágrafos), sensoriais e íntimas. "
            "Relação canônica: casados e cúmplices."
        )
        user = str(st.session_state.get("user_id", "") or "")
        usuario_key = f"{user}::mary" if user else "anon::mary"

        # Indicativo (somente leitura) do status de casamento
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}
        casados = bool(f.get("casados", True))
        container.caption(f"Estado da relação: **{'Casados' if casados else '—'}**")
