# characters/mary/service.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict, Tuple
import re, time, random

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

# === Janela por modelo e orçamento ===
MODEL_WINDOWS = {
    "anthropic/claude-3.5-haiku": 200_000,
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
    "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
    "deepseek/deepseek-chat-v3-0324": 32_000,
    # adicione aqui outros modelos usados no app
}
DEFAULT_WINDOW = 32_000

def _get_window_for(model: str) -> int:
    return MODEL_WINDOWS.get((model or "").strip(), DEFAULT_WINDOW)

def _budget_slices(model: str) -> tuple[int, int, int]:
    """
    Retorna (hist_budget, meta_budget, out_budget_base) em tokens.
    - histórico ~ 60%, meta (system+fatos+resumos) ~ 20%, saída ~ 20%.
    - garante piso razoável para histórico.
    """
    win = _get_window_for(model)
    hist = max(8_000, int(win * 0.60))
    meta = int(win * 0.20)
    outb = int(win * 0.20)
    return hist, meta, outb

def _safe_max_output(win: int, prompt_tokens: int) -> int:
    """
    Reserva espaço de saída sem estourar a janela (mínimo 512).
    """
    alvo = int(win * 0.20)
    sobra = max(0, win - prompt_tokens - 256)
    return max(512, min(alvo, sobra))

# === Mini-sumarizadores ===
def _heuristic_summarize(texto: str, max_bullets: int = 10) -> str:
    """
    Compacta texto grande em bullets telegráficos (fallback sem LLM).
    """
    texto = re.sub(r"\s+", " ", (texto or "").strip())
    sent = re.split(r"(?<=[\.\!\?])\s+", texto)
    sent = [s.strip() for s in sent if s.strip()]
    return " • " + "\n • ".join(sent[:max_bullets])

def _llm_summarize(model: str, user_chunk: str) -> str:
    """
    Usa o roteador para resumir um bloco antigo. Se der erro, cai no heurístico.
    """
    seed = (
        "Resuma em 6–10 frases telegráficas, somente fatos duráveis (decisões, nomes, locais, tempo, "
        "gestos/itens fixos e rumo da cena). Proibido diálogo literal."
    )
    try:
        data, used_model, provider = route_chat_strict(model, {
            "model": model,
            "messages": [
                {"role": "system", "content": seed},
                {"role": "user", "content": user_chunk}
            ],
            "max_tokens": 220,
            "temperature": 0.2,
            "top_p": 0.9
        })
        txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        return txt.strip() or _heuristic_summarize(user_chunk)
    except Exception:
        return _heuristic_summarize(user_chunk)

# ===== Blocos de system (slots) =====
def _build_system_block(persona_text: str,
                        rolling_summary: str,
                        sensory_focus: str,
                        nsfw_hint: str,
                        scene_loc: str,
                        entities_line: str,
                        evidence: str,
                        scene_time: str = "") -> str:
    persona_text = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()
    entities_line = (entities_line or "—").strip()

    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (
        f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
        "sempre integradas à ação (jamais em lista)."
    )
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
    rules = (
        "CONTINUIDADE: não mude tempo/lugar sem pedido explícito do usuário. "
        "Use MEMÓRIA e ENTIDADES abaixo como **fonte de verdade**. "
        "Se um nome/endereço não estiver salvo na MEMÓRIA/ENTIDADES, **não invente**: admita e convide o usuário a confirmar em 1 linha (sem forçar pergunta em todo turno)."
    )
    safety = "LIMITES: adultos; consentimento; nada ilegal."

    evidence_block = f"EVIDÊNCIA RECENTE (resumo ultra-curto de falas do usuário): {evidence or '—'}"

    return "\n\n".join([
        persona_text,
        length,
        sensory,
        nsfw_hint,
        rules,
        f"MEMÓRIA (canon curto): {rolling_summary}",
        f"ENTIDADES: {entities_line}",
        f"CONTINUIDADE: {continuity}",
        evidence_block,
        safety,
    ])

# ===== Robustez de chamada (retry + failover) =====
def _looks_like_cloudflare_5xx(err_text: str) -> bool:
    if not err_text:
        return False
    s = err_text.lower()
    return ("cloudflare" in s) and any(code in s for code in ["500", "502", "503", "504"])

def _robust_chat_call(model: str, messages: List[Dict[str, str]], *,
                      max_tokens: int = 1536, temperature: float = 0.7, top_p: float = 0.95,
                      fallback_models: List[str] | None = None) -> Tuple[Dict, str, str]:
    attempts = 3
    last_err = ""
    for i in range(attempts):
        try:
            return route_chat_strict(model, {
                "model": model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p
            })
        except Exception as e:
            last_err = str(e)
            if _looks_like_cloudflare_5xx(last_err) or "OpenRouter 502" in last_err:
                time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4))
                continue
            break
    if fallback_models:
        for fb in fallback_models:
            try:
                return route_chat_strict(fb, {
                    "model": fb, "messages": messages,
                    "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p
                })
            except Exception as e2:
                last_err = str(e2)
    synthetic = {
        "choices": [{"message": {"content":
            "Amor… tive um tropeço técnico agora, mas já mantive nosso fio e cenário. "
            "Me diz numa linha o próximo passo que você quer e eu sigo daí — sem perder o ritmo."
        }}]
    }
    return synthetic, model, "synthetic-fallback"

# ===== Utilidades de memória/entidades =====
_ENTITY_KEYS = ("club_name", "club_address", "club_alias", "club_contact", "club_ig")

def _entities_to_line(f: Dict) -> str:
    parts = []
    for k in _ENTITY_KEYS:
        v = str(f.get(f"mary.entity.{k}", "") or "").strip()
        if v:
            parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else "—"

def _compact_user_evidence(docs: List[Dict], max_chars: int = 320) -> str:
    """Concatena as últimas 4 falas do usuário (sem assistente), reduzindo ruído."""
    snippets: List[str] = []
    for d in reversed(docs):
        u = (d.get("mensagem_usuario") or "").strip()
        if u:
            u = re.sub(r"\s+", " ", u)
            snippets.append(u)
        if len(snippets) >= 4:
            break
    s = " | ".join(reversed(snippets))[:max_chars]
    return s

_CLUB_PAT = re.compile(
    r"\b(clube|club|casa)\s+([A-ZÀ-Üa-zà-ü0-9][\wÀ-ÖØ-öø-ÿ'’\- ]{1,40})\b", re.I
)
_ADDR_PAT = re.compile(
    r"\b(rua|av\.?|avenida|al\.?|alameda|rod\.?|rodovia)\s+[^,]{1,50},?\s*\d{1,5}\b", re.I
)
_IG_PAT = re.compile(
    r"(?:instagram\.com/|@)([A-Za-z0-9_.]{2,30})"
)

def _extract_and_store_entities(usuario_key: str, user_text: str, assistant_text: str) -> None:
    """Extrai entidades prováveis e persiste se fizer sentido (não sobrescreve agressivamente)."""
    try:
        f = get_facts(usuario_key) or {}
    except Exception:
        f = {}

    # Nome/alias do clube (prioriza o que veio do usuário)
    m = _CLUB_PAT.search(user_text or "") or _CLUB_PAT.search(assistant_text or "")
    if m:
        raw = m.group(0).strip()
        name = re.sub(r"\s+", " ", raw)
        cur = str(f.get("mary.entity.club_name", "") or "").strip()
        if not cur or len(name) >= len(cur):
            set_fact(usuario_key, "mary.entity.club_name", name, {"fonte": "extracted"})

    # Endereço
    a = _ADDR_PAT.search(user_text or "") or _ADDR_PAT.search(assistant_text or "")
    if a:
        addr = a.group(0).strip()
        cur = str(f.get("mary.entity.club_address", "") or "").strip()
        if not cur or len(addr) >= len(cur):
            set_fact(usuario_key, "mary.entity.club_address", addr, {"fonte": "extracted"})

    # Instagram/contato
    ig = _IG_PAT.search(user_text or "") or _IG_PAT.search(assistant_text or "")
    if ig:
        handle = ig.group(1).strip("@")
        cur = str(f.get("mary.entity.club_ig", "") or "").strip()
        if not cur:
            set_fact(usuario_key, "mary.entity.club_ig", "@"+handle, {"fonte": "extracted"})

# ===== Aviso de memória (resumo/poda) =====
def _mem_drop_warn(report: dict) -> None:
    """
    Mostra um aviso visual quando houve perda/compactação de memória.
    Usa st.info uma vez por turno.
    """
    if not report:
        return
    summarized = int(report.get("summarized_pairs", 0))
    trimmed    = int(report.get("trimmed_pairs", 0))
    hist_tokens = int(report.get("hist_tokens", 0))
    hist_budget = int(report.get("hist_budget", 0))

    if summarized or trimmed:
        msg = []
        if summarized:
            msg.append(f"**{summarized}** turnos antigos **foram resumidos**")
        if trimmed:
            msg.append(f"**{trimmed}** turnos verbatim **foram podados**")
        txt = " e ".join(msg)
        st.info(
            f"⚠️ Memória ajustada: {txt}. "
            f"(histórico: {hist_tokens}/{hist_budget} tokens). "
            "Se notar esquecimentos, peça um **‘recap curto’** ou fixe fatos na **Memória Canônica**.",
            icon="⚠️",
        )

class MaryService(BaseCharacter):
    id: str = "mary"
    display_name: str = "Mary"

    # ===== API =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::mary"

        # Memória/continuidade base
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # Foco sensorial rotativo
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
            "Vocês são casados e cúmplices."
            if nsfw_on else
            "NSFW: BLOQUEADO. Não descreva ato sexual explícito; use tensão e sugestão."
        )

        # ===== SUMÁRIO + ENTIDADES + EVIDÊNCIA =====
        rolling = self._get_rolling_summary(usuario_key)  # v2
        try:
            f_all = get_facts(usuario_key) or {}
        except Exception:
            f_all = {}
        entities_line = _entities_to_line(f_all)

        # pega docs crus para compor o evidence curto do usuário
        try:
            docs = get_history_docs(usuario_key) or []
        except Exception:
            docs = []
        evidence = _compact_user_evidence(docs, max_chars=320)

        # System único com slots
        system_block = _build_system_block(
            persona_text=persona_text,
            rolling_summary=rolling,
            sensory_focus=foco,
            nsfw_hint=nsfw_hint,
            scene_loc=local_atual or "—",
            entities_line=entities_line,
            evidence=evidence,
            scene_time=st.session_state.get("momento_atual", "")
        )

        # === Histórico com orçamento por modelo + relatório de memória ===
        hist_msgs = self._montar_historico(usuario_key, history_boot, model, verbatim_ultimos=6)

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + [{"role": "system", "content": (
                f"LOCAL_ATUAL: {local_atual or '—'}. "
                "Regra dura: NÃO mude tempo/lugar sem pedido explícito do usuário."
            )}]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # Aviso visual se houve resumo/poda neste turno
        try:
            _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception:
            pass

        # --- orçamento de saída dinâmico ---
        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m["content"]) for m in messages)
        except Exception:
            prompt_tokens = 0
        max_out = _safe_max_output(win, prompt_tokens)

        # Chamada robusta
        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]
        data, used_model, provider = _robust_chat_call(
            model, messages, max_tokens=max_out, temperature=0.7, top_p=0.95, fallback_models=fallbacks
        )
        texto = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

        # Sentinela: detectar sinais de esquecimento explícito na resposta
        try:
            forgot_pat = re.compile(
                r"\b(n[ãa]o (lembro|recordo)|quem (é voc[êe]|[ée] voc[êe])|me relembre|o que est[aá]vamos)\b",
                re.I
            )
            if forgot_pat.search(texto or ""):
                st.warning("🧠 A IA sinalizou possível esquecimento. Se necessário, peça **‘recap curto’** ou fixe fatos na Memória Canônica.")
        except Exception:
            pass

        # Persistência da interação
        save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")

        # Atualiza ENTIDADES (puxando do user/assistant atuais)
        try:
            _extract_and_store_entities(usuario_key, prompt, texto)
        except Exception:
            pass

        # Resumo rolante V2 (toda rodada, curto)
        try:
            self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception:
            pass

        # Placeholder sugestivo leve
        try:
            ph = self._suggest_placeholder(texto, local_atual)
            st.session_state["suggestion_placeholder"] = ph
            st.session_state["last_assistant_message"] = texto
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        # Sinaliza failover
        try:
            if provider == "synthetic-fallback":
                st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")
            elif used_model and "together/" in used_model:
                st.caption(f"↪️ Failover automático: **{used_model}**.")
        except Exception:
            pass

        return texto

    # ===== utilidades =====
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
        """Memória canônica curta + pistas fortes (não exceder)."""
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}
        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")

        casados = bool(f.get("casados", True))
        blocos.append(f"casados={casados}")

        # últimas entidades (se existirem)
        ent_line = _entities_to_line(f)
        if ent_line and ent_line != "—":
            blocos.append(f"entidades=({ent_line})")

        # evento opcional
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
            "Regras: relação de casal (casados) e confiança são base. "
            "Use ENTIDADES como fonte de verdade para nomes/endereços; se estiver ausente, não invente."
        )
        return pin

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 6,
    ) -> List[Dict[str, str]]:
        """
        Monta histórico usando orçamento por modelo:
          - Últimos N turnos verbatim preservados.
          - Antigos viram [RESUMO-*] em 1–3 camadas.
          - Se necessário, poda verbatim mais antigo.
        Além disso, grava um relatório em st.session_state['_mem_drop_report'].
        """
        win = _get_window_for(model)
        hist_budget, meta_budget, _ = _budget_slices(model)

        docs = get_history_docs(usuario_key)
        if not docs:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        # Constrói pares user/assistant em ordem cronológica
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_mary") or "").strip()
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})

        if not pares:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos  = pares[:len(pares) - len(verbatim)]

        msgs: List[Dict[str, str]] = []
        summarized_pairs = 0
        trimmed_pairs = 0

        # Resumo em camadas
        if antigos:
            summarized_pairs = len(antigos) // 2  # aproxima nº de turnos (pares)
            bloco = "\n\n".join(m["content"] for m in antigos)
            resumo = _llm_summarize(model, bloco)
            resumo_layers = [resumo]

            def _count_total_sim(resumo_texts: List[str]) -> int:
                sim_msgs = [{"role": "system", "content": f"[RESUMO-{i+1}]\n{r}"} for i, r in enumerate(resumo_texts)]
                sim_msgs += verbatim
                return sum(toklen(m["content"]) for m in sim_msgs)

            while _count_total_sim(resumo_layers) > hist_budget and len(resumo_layers) < 3:
                resumo_layers[0] = _llm_summarize(model, resumo_layers[0])

            for i, r in enumerate(resumo_layers, start=1):
                msgs.append({"role": "system", "content": f"[RESUMO-{i}]\n{r}"})

        # Injeta verbatim
        msgs.extend(verbatim)

        # Poda se ainda exceder orçamento
        def _hist_tokens(mm: List[Dict[str, str]]) -> int:
            return sum(toklen(m["content"]) for m in mm)

        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]
                trimmed_pairs += 1
            else:
                verbatim = []
            # Reconstrói
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim

        # Report para aviso visual
        hist_tokens = _hist_tokens(msgs)
        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": summarized_pairs,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": hist_tokens,
            "hist_budget": hist_budget,
        }

        return msgs if msgs else history_boot[:]

    # ===== Resumo rolante V2 (todo turno, curto) =====
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = get_facts(usuario_key) or {}
            return str(f.get("mary.rs.v2", "") or f.get("mary.rolling_summary", "") or "")
        except Exception:
            return ""

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        seed = (
            "Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos duráveis: "
            "nomes próprios (ex.: clube), endereços/links, relação (casados), local/tempo atual, "
            "itens/gestos fixos e rumo do enredo. "
            "Proíba diálogos literais; seja telegráfico e informativo."
        )
        try:
            data, used_model, provider = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": f"USER:\n{last_user}\n\nMARY:\n{last_assistant}"}
                ],
                "max_tokens": 180,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            resumo = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
            if resumo:
                set_fact(usuario_key, "mary.rs.v2", resumo, {"fonte": "auto_summary"})
        except Exception:
            pass

    # ===== Placeholder leve =====
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Continua do ponto exato… me conduz."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Quero — descreve devagar o próximo passo."
        if scene_loc:
            return f"No {scene_loc} mesmo — fala baixinho no meu ouvido."
        return "Mantém o cenário e dá o próximo passo com calma."

    # ===== Sidebar (somente leitura) =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Mary — Esposa Cúmplice** • Respostas longas (4–7 parágrafos), sensoriais e íntimas. "
            "Relação canônica: casados e cúmplices."
        )
        user = str(st.session_state.get("user_id", "") or "")
        usuario_key = f"{user}::mary" if user else "anon::mary"
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}
        casados = bool(f.get("casados", True))
        ent = _entities_to_line(f)
        rs = (f.get("mary.rs.v2") or "")[:200]
        container.caption(f"Estado da relação: **{'Casados' if casados else '—'}**")
        if ent and ent != "—":
            container.caption(f"Entidades salvas: {ent}")
        if rs:
            container.caption("Resumo rolante ativo (v2).")
