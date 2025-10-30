# characters/mary/service.py
from __future__ import annotations

import re, time, random
from typing import List, Dict, Tuple
import streamlit as st

from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
from core.ultra import critic_review, polish
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact
)
from core.tokens import toklen
import json
# === Tool Calling: definição de ferramentas disponíveis para a Mary ===
# Você pode ampliar livremente esta lista conforme precisar.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Retorna fatos canônicos curtos do casal e entidades salvas (linha compacta).",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Salva ou atualiza um fato canônico (chave/valor) para Mary.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"]
            }
        }
    },
]
def _current_user_key() -> str:
    """
    Devolve SEMPRE a mesma chave de usuário para Mary.
    Se não tiver login, usa 'anon::mary'.
    """
    uid = str(st.session_state.get("user_id", "") or "").strip()
    if not uid:
        return "anon::mary"
    return f"{uid}::mary"


# ====== MEMÓRIA TEMÁTICA (gravar e recuperar) ======

# frases que disparam o modo "grava o que a MARY VAI FALAR"
SAVE_PROMPTS = (
    "mary, use sua ferramenta de memória para registrar",
    "mary use sua ferramenta de memória para registrar",
    "mary, registre na sua memória",
    "mary registre na sua memória",
    "mary, salve na memória",
    "mary salve na memória",
)

def _user_is_asking_to_save_assistant_version(prompt: str) -> bool:
    p = (prompt or "").lower().strip()
    if not p:
        return False
    return any(p.startswith(s) for s in SAVE_PROMPTS)


def _detect_thematic_tags_from_prompt(prompt: str) -> list[str]:
    """
    Vê se o usuário está perguntando de um evento específico.
    Aqui já tratamos o caso 'Carlos e Beatriz'.
    """
    p = (prompt or "").lower()
    tags: list[str] = []

    if "carlos" in p and "beatriz" in p:
        tags.append("mary.evento.carlos_beatriz")

    return tags


def _get_thematic_memories_for_tags(user_key: str, tags: list[str]) -> str:
    """
    Dado uma lista de chaves que podem ter sido salvas via set_fact,
    monta um bloco único pra injetar no system.
    """
    if not tags:
        return ""
    parts: list[str] = []
    for tag in tags:
        try:
            val = get_fact(user_key, tag, None)
        except Exception:
            val = None
        if val:
            parts.append(f"{tag}: {val}")
    return "\n".join(parts)


def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
    """
    Execução das ferramentas chamadas via Tool Calling.
    Retorna SEMPRE string (conteúdo que será repassado ao modelo como `tool` message).
    """
    try:
        if name == "get_memory_pin":
            return self._build_memory_pin(usuario_key, st.session_state.get("user_id","") or "")
        if name == "set_fact":
            k = (args or {}).get("key", "")
            v = (args or {}).get("value", "")
            set_fact(usuario_key, k, v, {"fonte": "tool_call"})
            # invalidar cache leve, se houver
            try:
                clear_user_cache(usuario_key)  # se existir no seu projeto
            except Exception:
                pass
            return f"OK: {k}={v}"
        return "ERRO: ferramenta desconhecida"
    except Exception as e:
        return f"ERRO: {e}"


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
            "Fale em primeira pessoa (eu). Tom insinuante e sutil. "
            "Traga 1 pista sensorial integrada à ação. "
            "Sem metacena, sem listas. 2–4 frases por parágrafo; 4–7 parágrafos."
        )
        return txt, []

# === Janela por modelo e orçamento ===
MODEL_WINDOWS = {
    "anthropic/claude-3.5-haiku": 200_000,
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
    "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
    "deepseek/deepseek-chat-v3-0324": 32_000,
    "inclusionai/ling-1t": 64_000,  # ajuste se o provedor publicar outro contexto
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
    """Reserva espaço de saída sem estourar a janela (mínimo 512)."""
    alvo = int(win * 0.20)
    sobra = max(0, win - prompt_tokens - 256)
    return max(512, min(alvo, sobra))

# =========================
# Cache leve (facts/history)
# =========================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos
_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_timestamps: Dict[str, float] = {}

def _purge_expired_cache():
    now = time.time()
    # facts
    for k in list(_cache_facts.keys()):
        if now - _cache_timestamps.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None)
            _cache_timestamps.pop(f"facts_{k}", None)
    # history
    for k in list(_cache_history.keys()):
        if now - _cache_timestamps.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None)
            _cache_timestamps.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None)
    _cache_timestamps.pop(f"facts_{user_key}", None)
    _cache_history.pop(user_key, None)
    _cache_timestamps.pop(f"hist_{user_key}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_facts and (now - _cache_timestamps.get(f"facts_{user_key}", 0) < CACHE_TTL):
        return _cache_facts[user_key]
    try:
        f = get_facts(user_key) or {}
    except Exception:
        f = {}
    _cache_facts[user_key] = f
    _cache_timestamps[f"facts_{user_key}"] = now
    return f

def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_history and (now - _cache_timestamps.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_history[user_key]
    try:
        docs = get_history_docs(user_key) or []
    except Exception:
        docs = []
    _cache_history[user_key] = docs
    _cache_timestamps[f"hist_{user_key}"] = now
    return docs

# === Preferências do usuário ===
def _read_prefs(facts: Dict) -> Dict:
    """Lê preferências persistidas e define padrões seguros."""
    prefs = {
        "nivel_sensual": str(facts.get("mary.pref.nivel_sensual", "") or "sutil").lower(),   # sutil | media | alta
        "ritmo": str(facts.get("mary.pref.ritmo", "") or "lento").lower(),                   # lento | normal | rapido
        "tamanho_resposta": str(facts.get("mary.pref.tamanho_resposta", "") or "media").lower(),  # curta | media | longa
        "evitar_topicos": facts.get("mary.pref.evitar_topicos", []) or [],
        "temas_favoritos": facts.get("mary.pref.temas_favoritos", []) or [],
    }
    if isinstance(prefs["evitar_topicos"], str):
        prefs["evitar_topicos"] = [prefs["evitar_topicos"]]
    if isinstance(prefs["temas_favoritos"], str):
        prefs["temas_favoritos"] = [prefs["temas_favoritos"]]
    return prefs

def _prefs_line(prefs: Dict) -> str:
    """Linha barata para o system com instruções de estilo dinâmicas."""
    evitar = ", ".join(prefs.get("evitar_topicos") or [])
    temas  = ", ".join(prefs.get("temas_favoritos") or [])
    return (
        f"PREFERÊNCIAS: nível={prefs.get('nivel_sensual','sutil')}; ritmo={prefs.get('ritmo','lento')}; "
        f"tamanho={prefs.get('tamanho_resposta','media')}; "
        f"evitar=[{evitar or '—'}]; temas_favoritos=[{temas or '—'}]. "
        "Use insinuação elegante; evite listas de atos e aceleração artificial."
    )

# === Mini-sumarizadores ===
def _heuristic_summarize(texto: str, max_bullets: int = 10) -> str:
    """Compacta texto grande em bullets telegráficos (fallback sem LLM)."""
    texto = re.sub(r"\s+", " ", (texto or "").strip())
    sent = re.split(r"(?<=[\.\!\?])\s+", texto)
    sent = [s.strip() for s in sent if s.strip()]
    return " • " + "\n • ".join(sent[:max_bullets])

def _llm_summarize(model: str, user_chunk: str) -> str:
    """Usa o roteador para resumir um bloco antigo. Se der erro, cai no heurístico."""
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
                        prefs_line: str = "",
                        scene_time: str = "") -> str:
    persona_text = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()
    entities_line = (entities_line or "—").strip()
    prefs_line = (prefs_line or "PREFERÊNCIAS: nível=sutil; ritmo=lento; tamanho=media.").strip()

    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (
        f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
        "sempre integradas à ação (jamais em lista)."
    )
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
    rules = (
        "CONTINUIDADE: não mude tempo/lugar sem pedido explícito do usuário. "
        "Use MEMÓRIA e ENTIDADES abaixo como **fonte de verdade**. "
        "Se um nome/endereço não estiver salvo na MEMÓRIA/ENTIDADES, **não invente**; convide o usuário a confirmar em 1 linha."
    )
    # (mantido conforme pedido – sem alteração do safety/hints NSFW)
    safety = "LIMITES: adultos; consentimento; nada ilegal."
    evidence_block = f"EVIDÊNCIA RECENTE (resumo ultra-curto de falas do usuário): {evidence or '—'}"

    return "\n\n".join([
        persona_text,
        prefs_line,
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

def _robust_chat_call(
    model: str,
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 1536,
    temperature: float = 0.7,
    top_p: float = 0.95,
    fallback_models: List[str] | None = None,
    tools: List[Dict] | None = None,
) -> Tuple[Dict, str, str]:
    attempts = 3
    last_err = ""
    for i in range(attempts):
        try:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
            }

            # 👉 se o sidebar ligou tool-calling, o service passou tools
            if tools:
                payload["tools"] = tools

            # 👉 JSON Mode do sidebar
            if st.session_state.get("json_mode_on", False):
                payload["response_format"] = {"type": "json_object"}

            # 👉 LoRA Together opcional
            adapter_id = (st.session_state.get("together_lora_id") or "").strip()
            if adapter_id and (model or "").startswith("together/"):
                payload["adapter_id"] = adapter_id

            # ✅ agora sim podemos chamar o router
            return route_chat_strict(model, payload)

        except Exception as e:
            last_err = str(e)
            # se for erro “instável”, tenta de novo
            if _looks_like_cloudflare_5xx(last_err) or "OpenRouter 502" in last_err:
                time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4))
                continue
            break  # erro real -> sai do loop

    # ===== FALLBACKS =====
    if fallback_models:
        for fb in fallback_models:
            try:
                payload_fb = {
                    "model": fb,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                }

                if tools:
                    payload_fb["tools"] = tools

                if st.session_state.get("json_mode_on", False):
                    payload_fb["response_format"] = {"type": "json_object"}

                adapter_id = (st.session_state.get("together_lora_id") or "").strip()
                if adapter_id and (fb or "").startswith("together/"):
                    payload_fb["adapter_id"] = adapter_id

                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)

    # ===== fallback sintético final =====
    synthetic = {
        "choices": [{
            "message": {
                "content": (
                    "Amor… o provedor caiu agora, mas mantive o cenário. "
                    "Me diz numa linha o que você quer fazer e eu continuo."
                )
            }
        }]
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

_CLUB_PAT = re.compile(r"\b(clube|club|casa)\s+([A-ZÀ-Üa-zà-ü0-9][\wÀ-ÖØ-öø-ÿ'’\- ]{1,40})\b", re.I)
_ADDR_PAT = re.compile(r"\b(rua|av\.?|avenida|al\.?|alameda|rod\.?|rodovia)\s+[^,]{1,50},?\s*\d{1,5}\b", re.I)
_IG_PAT   = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9_.]{2,30})")

def _extract_and_store_entities(usuario_key: str, user_text: str, assistant_text: str) -> None:
    """Extrai entidades prováveis e persiste se fizer sentido (não sobrescreve agressivamente)."""
    try:
        f = cached_get_facts(usuario_key) or {}
    except Exception:
        f = {}

    # Nome do clube/casa: usar group(2) (somente o nome), sem o prefixo "clube/casa"
    m = _CLUB_PAT.search(user_text or "") or _CLUB_PAT.search(assistant_text or "")
    if m:
        name = re.sub(r"\s+", " ", m.group(2)).strip()
        cur = str(f.get("mary.entity.club_name", "") or "").strip()
        if not cur or len(name) >= len(cur):
            set_fact(usuario_key, "mary.entity.club_name", name, {"fonte": "extracted"})
            clear_user_cache(usuario_key)

    a = _ADDR_PAT.search(user_text or "") or _ADDR_PAT.search(assistant_text or "")
    if a:
        addr = a.group(0).strip()
        cur = str(f.get("mary.entity.club_address", "") or "").strip()
        if not cur or len(addr) >= len(cur):
            set_fact(usuario_key, "mary.entity.club_address", addr, {"fonte": "extracted"})
            clear_user_cache(usuario_key)

    ig = _IG_PAT.search(user_text or "") or _IG_PAT.search(assistant_text or "")
    if ig:
        handle = ig.group(1).strip("@")
        cur = str(f.get("mary.entity.club_ig", "") or "").strip()
        if not cur:
            set_fact(usuario_key, "mary.entity.club_ig", "@"+handle, {"fonte": "extracted"})
            clear_user_cache(usuario_key)

# ===== Aviso de memória (resumo/poda) =====
def _mem_drop_warn(report: dict) -> None:
    """Mostra um aviso visual quando houve perda/compactação de memória."""
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

    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        """
        Executa uma ferramenta chamada pelo modelo.
        Sempre retorna STRING, para ser usada como mensagem de `tool`.
        """
        try:
            # 1) pegar user_display só uma vez
            user_display = st.session_state.get("user_id", "") or ""

            # 2) ferramenta básica: devolver pin de memória
            if name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, user_display)

            # 3) ferramenta básica: set_fact (genérica)
            if name == "set_fact":
                k = (args or {}).get("key", "") if isinstance(args, dict) else ""
                v = (args or {}).get("value", "") if isinstance(args, dict) else ""
                if not k:
                    return "ERRO: chave ('key') não informada."
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: {k}={v}"

            # 4) ferramenta específica: salvar evento narrativo da Mary
            #    name: "save_event"
            #    args esperados:
            #      - label: nome curto da memória (ex.: "carlos_beatriz")
            #      - content: texto completo que o modelo acabou de produzir
            # Se não vier label, vamos gerar um.
            if name == "save_event":
                label = ""
                content = ""
                if isinstance(args, dict):
                    label = (args.get("label") or "").strip()
                    content = (args.get("content") or "").strip()

                if not content:
                    # tenta pegar da última mensagem do assistente, se você estiver guardando
                    content = st.session_state.get("last_assistant_message", "").strip()

                if not content:
                    return "ERRO: nenhum conteúdo para salvar."

                # se não veio label, gera um rápido
                if not label:
                    # tenta achar nomes no conteúdo
                    low = content.lower()
                    if "carlos" in low and "beatriz" in low:
                        label = "carlos_beatriz"
                    else:
                        # fallback com timestamp curtinho
                        import time
                        label = f"evento_{int(time.time())}"

                fact_key = f"mary.evento.{label}"
                set_fact(usuario_key, fact_key, content, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: salvo em {fact_key}"

            # 5) se não reconheceu:
            return f"ERRO: ferramenta desconhecida: {name}"

        except Exception as e:
            return f"ERRO: {e}"

    # ===== API =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        usuario_key = _current_user_key()   # << primeiro
        persona_text, history_boot = self._load_persona()

        # Memória/continuidade base
        local_atual = self._safe_get_local(usuario_key)
        try:
            f_all = cached_get_facts(usuario_key) or {}
        except Exception:
            f_all = {}
        prefs = _read_prefs(f_all)
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

        # NSFW por usuário com nuance de preferência (seguro)
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        nivel = prefs.get("nivel_sensual", "sutil")
        if not nsfw_on:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Use sugestão, tensão e intimidade sem descrição explícita de atos; "
                "concentre-se em atmosfera, presença e condução confiante."
            )
        else:
            if nivel == "sutil":
                nsfw_hint = (
                    "NSFW: LIBERADO. **Insinuação elegante** e tensão crescente; foque em sensações e proximidade, "
                    "com genitais/ato gráfico."
                )
            elif nivel == "alta":
                nsfw_hint = (
                    "NSFW: LIBERADO. Intensifique descrição corporal, sensorial, pêlos pubianos, membro grosso, beijos de língua, mãos na bundas, grêlo pulsante, buceta molhada de tesão "
                    "e condução de ritmo; **descreva genitais, com ato sexual gráfico**."
                )
            else:  # 'media'
                nsfw_hint = (
                    "NSFW: LIBERADO. Sensualidade clara e progressiva; descreva sensações e ações sem pressa, demonstrar desejo sexual, sedução, vontade de prosseguir carícias, sensorial "
                    "com genitais/ato gráfico."
                )
        # =================================

        # ===== SUMÁRIO + ENTIDADES + EVIDÊNCIA =====
        rolling = self._get_rolling_summary(usuario_key)  # v2
        entities_line = _entities_to_line(f_all)

        try:
            docs = cached_get_history(usuario_key) or []
        except Exception:
            docs = []
        evidence = self._compact_user_evidence(docs, max_chars=320)

        # System único com slots (+ PREFERÊNCIAS)
        system_block = _build_system_block(
            persona_text=persona_text,
            rolling_summary=rolling,
            sensory_focus=foco,
            nsfw_hint=nsfw_hint,
            scene_loc=local_atual or "—",
            entities_line=entities_line,
            evidence=evidence,
            prefs_line=_prefs_line(prefs),
            scene_time=st.session_state.get("momento_atual", "")
        )

        # === LORE (memória longa)
        lore_msgs: List[Dict[str, str]] = []
        try:
            q = (prompt or "") + "\n" + (rolling or "")
            top = lore_topk(usuario_key, q, k=4, allow_tags=None)
            if top:
                lore_text = " | ".join(d.get("texto", "") for d in top if d.get("texto"))
                if lore_text:
                    lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception:
            pass

                # === Histórico com orçamento por modelo + relatório de memória ===
        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 10))  # << configurável via UI
        hist_msgs = self._montar_historico(
            usuario_key,
            history_boot,
            model,
            verbatim_ultimos=verbatim_ultimos,
        )

        # --- 3.a) coletar eventos salvos (mary.evento.*)
        eventos_block = ""
        try:
            # f_all já foi carregado lá em cima:
            #   try: f_all = cached_get_facts(usuario_key) or {}
            eventos = []
            for k, v in (f_all or {}).items():
                if k.startswith("mary.evento.") and v:
                    # tira só o sufixo pra ficar mais amigável
                    label = k.split("mary.evento.", 1)[-1]
                    eventos.append(f"- {label}: {str(v).strip()}")
            if eventos:
                # não exagerar: corta no máx. 800–1000 chars
                joined = "\n".join(eventos)[:1000]
                eventos_block = (
                    "EVENTOS_FIXOS_MARY:\n"
                    "Use estes registros como FONTE DE VERDADE para perguntas futuras do usuário.\n"
                    "Se o usuário disser 'lembra do encontro com Carlos e Beatriz?', recupere o evento correspondente.\n"
                    + joined
                )
        except Exception:
            eventos_block = ""

        # --- 3.b) montar messages final
        messages: List[Dict,] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + ([{"role": "system", "content": eventos_block}] if eventos_block else [])
            + lore_msgs
            + [{
                "role": "system",
                "content": (
                    f"LOCAL_ATUAL: {local_atual or '—'}. "
                    "Regra dura: NÃO mude tempo/lugar sem pedido explícito do usuário."
                )
            }]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )


        # Aviso visual se houve resumo/poda neste turno
        try:
            _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception:
            pass

        # --- orçamento de saída dinâmico (modulado por preferência de tamanho) ---
        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m["content"]) for m in messages)
        except Exception:
            prompt_tokens = 0
        base_out = _safe_max_output(win, prompt_tokens)
        size = prefs.get("tamanho_resposta", "media")
        mult = 1.0 if size == "media" else (0.75 if size == "curta" else 1.25)
        max_out = max(512, int(base_out * mult))

        # Temperatura conforme ritmo
        ritmo = prefs.get("ritmo", "lento")
        temperature = 0.6 if ritmo == "lento" else (0.9 if ritmo == "rapido" else 0.7)

        # Chamada robusta (Writer)
        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]
        # Preparar tools se Tool Calling estiver ativo
        tools_to_use = None
        if st.session_state.get("tool_calling_on", False):
            tools_to_use = TOOLS
        
        # Loop de Tool Calling (máximo 3 iterações para evitar loops infinitos)
        max_iterations = 3
        iteration = 0
        texto = ""
        
        tool_calls = []
        while iteration < max_iterations:
            iteration += 1
            
            # Chamada robusta (Writer) com tools
            data, used_model, provider = _robust_chat_call(
                model, messages, max_tokens=max_out, temperature=temperature, top_p=0.95, 
                fallback_models=fallbacks, tools=tools_to_use
            )
            
            # Extrair resposta
            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])
            
            # Se não há tool calls, termina o loop
            if not tool_calls or not st.session_state.get("tool_calling_on", False):
                break
            
            # Processar tool calls
            st.caption(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")
            
            # Adiciona a mensagem do assistente com tool_calls
            messages.append({
                "role": "assistant",
                "content": texto or None,
                "tool_calls": tool_calls
            })
            
            # Executa cada tool call
            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}")
                func_name = tc.get("function", {}).get("name", "")
                func_args_str = tc.get("function", {}).get("arguments", "{}")
                
                try:
                    # Parse dos argumentos
                    func_args = json.loads(func_args_str) if func_args_str else {}
                    
                    # Executa a ferramenta
                    result = self._exec_tool_call(func_name, func_args, usuario_key)
                    
                    # Adiciona resultado às mensagens
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result
                    })
                    
                    st.caption(f"  ✓ {func_name}: {result[:50]}...")
                    
                except Exception as e:
                    error_msg = f"ERRO ao executar {func_name}: {str(e)}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": error_msg
                    })
                    st.warning(f"⚠️ {error_msg}")
            
            # Se chegou aqui, há tool calls - continua o loop para nova chamada
            # (o modelo vai processar os resultados das tools e gerar resposta final)
        
        # Aviso se atingiu limite de iterações
        if iteration >= max_iterations and st.session_state.get("tool_calling_on", False):
            st.warning("⚠️ Limite de iterações de Tool Calling atingido. Resposta pode estar incompleta.")
        # Ultra IA (opcional): writer -> critic -> polisher
        try:
            if bool(st.session_state.get("ultra_ia_on", False)) and texto:
                critic_model = st.session_state.get("ultra_critic_model", model) or model
                notes = critic_review(critic_model, system_block, prompt, texto)
                texto = polish(model, system_block, prompt, texto, notes)
        except Exception:
            pass

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

        # ============================================================
        # Persistência da interação (com gravação automática de memórias)
        # ============================================================
        try:
            save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass

        # === 4.a) detectar se o usuário pediu gravação explícita ===
        # Gatilhos como: "use sua ferramenta de memória", "registre", "salve na memória"
        mem_triggers = ("use sua ferramenta de memória", "registre", "salve na memória", "gravar memória")
        if any(t in prompt.lower() for t in mem_triggers):
            try:
                label = ""
                # tenta identificar nomes no prompt para gerar nome de evento
                low = prompt.lower()
                if "carlos" in low and "beatriz" in low:
                    label = "carlos_beatriz"
                elif "clube" in low and "surpresa" in low:
                    label = "clube_surpresas"
                elif "praia" in low:
                    label = "praia"
                else:
                    import time
                    label = f"evento_{int(time.time())}"

                fact_key = f"mary.evento.{label}"
                set_fact(usuario_key, fact_key, texto, {"fonte": "auto_gravado"})
                clear_user_cache(usuario_key)
                st.caption(f"🧠 Memória fixa registrada automaticamente como: **{fact_key}**")
            except Exception as e:
                st.warning(f"⚠️ Falha ao registrar memória fixa: {e}")

        # === 4.b) Atualiza ENTIDADES
        try:
            _extract_and_store_entities(usuario_key, prompt, texto)
        except Exception:
            pass

        # === 4.c) Atualiza resumo rolante
        try:
            self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception:
            pass

        # === 4.d) Memória longa (lore)
        try:
            frag = f"[USER] {prompt}\n[MARY] {texto}"
            lore_save(usuario_key, frag, tags=["mary", "chat"])
        except Exception:
            pass

        # === 4.e) Placeholder e cache leve
        try:
            ph = self._suggest_placeholder(texto, local_atual)
            st.session_state["suggestion_placeholder"] = ph
            st.session_state["last_assistant_message"] = texto
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        # === 4.f) Sinaliza failover
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
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}
        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")

        casados = bool(f.get("casados", True))
        blocos.append(f"casados={casados}")

        ent_line = _entities_to_line(f)
        if ent_line and ent_line != "—":
            blocos.append(f"entidades=({ent_line})")

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
            "Regras: casal (casados) e confiança são base. "
            "Use ENTIDADES como fonte de verdade; se ausente, não invente."
        )
        return pin

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
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

        docs = cached_get_history(usuario_key)
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
            summarized_pairs = len(antigos) // 2
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
        def _hist_tokens(mm: List[Dict,]) -> int:
            return sum(toklen(m["content"]) for m in mm)

        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]
                trimmed_pairs += 1
            else:
                verbatim = []
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

    # ===== Rolling summary helpers =====
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("mary.rs.v2", "") or f.get("mary.rolling_summary", "") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("mary.rs.v2", "")
            last_update_ts = float(f.get("mary.rs.v2.ts", 0))
            now = time.time()
            if not last_summary:
                return True
            if now - last_update_ts > 300:  # 5 min
                return True
            if (len(last_user) + len(last_assistant)) > 100:
                return True
            return False
        except Exception:
            return True

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        if not self._should_update_summary(usuario_key, last_user, last_assistant):
            return
        seed = (
            "Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos duráveis: "
            "nomes próprios, endereços/links, relação (casados), local/tempo atual, "
            "itens/gestos fixos e rumo do enredo. Proíba diálogos literais."
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
                set_fact(usuario_key, "mary.rs.v2.ts", time.time(), {"fonte": "auto_summary"})
                clear_user_cache(usuario_key)
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

    # ===== Evidência concisa do usuário (últimas falas) =====
    def _compact_user_evidence(self, docs: List[Dict], max_chars: int = 320) -> str:
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

      # ===== Sidebar (somente leitura) =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Mary — Esposa Cúmplice** • Respostas insinuantes e sutis; 4–7 parágrafos. "
            "Relação canônica: casados e cúmplices."
        )

        # mesma chave usada no reply()
        usuario_key = _current_user_key()

        # facts do usuário
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}

        casados = bool(f.get("casados", True))
        ent = _entities_to_line(f)
        rs = (f.get("mary.rs.v2") or "")[:200]
        prefs = _read_prefs(f)

        container.caption(f"Estado da relação: **{'Casados' if casados else '—'}**")
        container.markdown("---")

        # toggles globais
        json_on = container.checkbox(
            "JSON Mode",
            value=bool(st.session_state.get("json_mode_on", False))
        )
        tool_on = container.checkbox(
            "Tool-Calling",
            value=bool(st.session_state.get("tool_calling_on", False))
        )
        st.session_state["json_mode_on"] = json_on
        st.session_state["tool_calling_on"] = tool_on

        lora = container.text_input(
            "Adapter ID (Together LoRA) — opcional",
            value=st.session_state.get("together_lora_id", "")
        )
        st.session_state["together_lora_id"] = lora

        if ent and ent != "—":
            container.caption(f"Entidades salvas: {ent}")
        if rs:
            container.caption("Resumo rolante ativo (v2).")
        container.caption(
            f"Prefs: nível={prefs.get('nivel_sensual')}, "
            f"ritmo={prefs.get('ritmo')}, "
            f"tamanho={prefs.get('tamanho_resposta')}"
        )

        # =====================================================
        # 1) DEBUG – mostrar tudo que o get_facts(...) trouxe
        # =====================================================
        with container.expander("🔎 DEBUG – facts brutos", expanded=False):
            if not f:
                st.caption("⚠️ Nenhum fact retornado para esta chave de usuário.")
                st.code(usuario_key)
            else:
                st.caption(f"User key: `{usuario_key}`")
                for k, v in f.items():
                    vs = str(v)
                    if len(vs) > 120:
                        vs = vs[:120] + "..."
                    st.write(f"- **{k}** = {vs}")

        # =====================================================
        # 2) Memórias fixas de Mary (mary.evento.*)
        # =====================================================
        with container.expander("🧠 Memórias fixas de Mary", expanded=True):
            try:
                f_all = f  # já temos
            except Exception:
                f_all = {}

            # pega só o que começar com mary.evento.
            eventos = {
                k: v for k, v in f_all.items()
                if isinstance(k, str) and k.startswith("mary.evento.")
            }

            if not eventos:
                container.caption(
                    "Nenhuma memória salva ainda.\n"
                    "Ex.: **Mary, use sua ferramenta de memória para registrar o fato: ...**"
                )
            else:
                for ev_key, ev_val in sorted(eventos.items()):
                    nome_curto = ev_key.replace("mary.evento.", "")
                    container.markdown(f"**{nome_curto}**")
                    container.caption(str(ev_val)[:280] + ("..." if len(str(ev_val)) > 280 else ""))

                    col1, col2 = container.columns([1, 1])
                    with col1:
                        if container.button("🗑 Apagar", key=f"del_{usuario_key}_{ev_key}"):
                            try:
                                from core.repositories import delete_fact
                            except Exception:
                                delete_fact = None
                            if delete_fact:
                                delete_fact(usuario_key, ev_key)
                            clear_user_cache(usuario_key)
                            container.success(f"Memória **{nome_curto}** apagada.")
                            st.experimental_rerun()
                    with col2:
                        container.caption(ev_key)


