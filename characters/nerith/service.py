# characters/nerith/service.py
# VERSÃO COMPACTA E ROBUSTA – pronta para colar
from __future__ import annotations
from .comics import render_comic_button
import re, time, random, json
from typing import List, Dict, Tuple, Optional
import streamlit as st

# ==== Núcleo do projeto (mantém seus imports originais) ====
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
from core.ultra import critic_review, polish
from core.repositories import (
    save_interaction, get_history_docs, get_facts, get_fact, last_event, set_fact
)
from core.tokens import toklen

# ==== Janela/Orçamento por modelo ====
MODEL_WINDOWS = {
    "anthropic/claude-3.5-haiku": 200_000,
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
    "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
    "deepseek/deepseek-chat-v3-0324": 32_000,
    "inclusionai/ling-1t": 64_000,
}
DEFAULT_WINDOW = 32_000

def _get_window_for(model: str) -> int:
    return MODEL_WINDOWS.get((model or "").strip(), DEFAULT_WINDOW)

def _budget_slices(model: str) -> tuple[int, int, int]:
    win = _get_window_for(model)
    hist = max(8_000, int(win * 0.60))
    meta = int(win * 0.20)
    outb = int(win * 0.20)
    return hist, meta, outb

def _safe_max_output(win: int, prompt_tokens: int) -> int:
    alvo = int(win * 0.20)
    sobra = max(0, win - prompt_tokens - 256)
    return max(512, min(alvo, sobra))

# ==== NSFW (opcional) ====
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# ==== PERSONA (com fallback seguro) ====
try:
    # characters/nerith/persona.py deve expor get_persona() -> Tuple[str, List[Dict[str,str]]]
    from .persona import get_persona  # type: ignore
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é **Nerith**, elfa guerreira de Elysarix. "
            "Tom direto, presença física, proteção ativa e coerência de cena. "
            "Use MEMÓRIA/ENTIDADES como fonte de verdade; não mude tempo/lugar sem pedido."
        )
        history_boot = [{
            "role": "assistant",
            "content": "*A luz azul vaza do guarda-roupa. Eu piso no quarto, alta, pele azul em brilho baixo.* "
                       "\"Janio... acorde. Sou Nerith. Você me chamou.\""
        }]
        return txt, history_boot

# ==== CACHE LEVE ====
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))
_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_ts: Dict[str, float] = {}

def _purge_expired_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_ts.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None); _cache_ts.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_ts.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None); _cache_ts.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None); _cache_ts.pop(f"facts_{user_key}", None)
    _cache_history.pop(user_key, None); _cache_ts.pop(f"hist_{user_key}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_facts and (now - _cache_ts.get(f"facts_{user_key}", 0) < CACHE_TTL):
        return _cache_facts[user_key]
    try:
        f = get_facts(user_key) or {}
    except Exception:
        f = {}
    _cache_facts[user_key] = f; _cache_ts[f"facts_{user_key}"] = now
    return f

def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_history and (now - _cache_ts.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_history[user_key]
    try:
        docs = get_history_docs(user_key) or []
    except Exception:
        docs = []
    _cache_history[user_key] = docs; _cache_ts[f"hist_{user_key}"] = now
    return docs

# ==== Preferências (opcional) ====
def _read_prefs(facts: Dict) -> Dict:
    prefs = {
        "nivel_sensual": str(facts.get("nerith.pref.nivel_sensual", "sutil")).lower(),
        "ritmo": str(facts.get("nerith.pref.ritmo", "lento")).lower(),
        "tamanho_resposta": str(facts.get("nerith.pref.tamanho_resposta", "media")).lower(),
        "evitar_topicos": facts.get("nerith.pref.evitar_topicos", []) or [],
        "temas_favoritos": facts.get("nerith.pref.temas_favoritos", []) or [],
    }
    if isinstance(prefs["evitar_topicos"], str): prefs["evitar_topicos"] = [prefs["evitar_topicos"]]
    if isinstance(prefs["temas_favoritos"], str): prefs["temas_favoritos"] = [prefs["temas_favoritos"]]
    return prefs

def _prefs_line(prefs: Dict) -> str:
    evitar = ", ".join(prefs.get("evitar_topicos") or [])
    temas  = ", ".join(prefs.get("temas_favoritos") or [])
    return (
        f"PREFERÊNCIAS: nível={prefs.get('nivel_sensual','sutil')}; ritmo={prefs.get('ritmo','lento')}; "
        f"tamanho={prefs.get('tamanho_resposta','media')}; evitar=[{evitar or '—'}]; "
        f"temas_favoritos=[{temas or '—'}]."
    )

# ==== Mini-sumários ====
def _heuristic_summarize(texto: str, max_bullets: int = 10) -> str:
    texto = re.sub(r"\s+", " ", (texto or "").strip())
    sent = re.split(r"(?<=[\.\!\?])\s+", texto)
    sent = [s.strip() for s in sent if s.strip()]
    return " • " + "\n • ".join(sent[:max_bullets])

def _llm_summarize(model: str, user_chunk: str) -> str:
    seed = ("Resuma em 6–10 frases telegráficas; fatos duráveis (decisões, nomes, locais, relação/rumo). "
            "Proibido diálogo literal.")
    try:
        data, _, _ = route_chat_strict(model, {
            "model": model,
            "messages": [{"role": "system", "content": seed},
                         {"role": "user", "content": user_chunk}],
            "max_tokens": 220, "temperature": 0.2, "top_p": 0.9
        })
        txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        return txt.strip() or _heuristic_summarize(user_chunk)
    except Exception:
        return _heuristic_summarize(user_chunk)

# ==== System block ====
def _build_system_block(persona_text: str, rolling_summary: str, sensory_focus: str,
                        nsfw_hint: str, scene_loc: str, entities_line: str,
                        evidence: str, prefs_line: str = "", scene_time: str = "") -> str:
    persona_text = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()
    entities_line = (entities_line or "—").strip()
    prefs_line = (prefs_line or "PREFERÊNCIAS: nível=sutil; ritmo=lento; tamanho=media.").strip()
    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
               "sempre integradas à ação (jamais em lista).")
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; linguagem direta; sem metacena."
    rules = ("CONTINUIDADE: não mude tempo/lugar sem pedido explícito do usuário. "
             "Use MEMÓRIA/ENTIDADES como fonte de verdade. Se dado ausente, convide a confirmar em 1 linha.")
    safety = "LIMITES: adultos; consentimento; nada ilegal."
    evidence_block = f"EVIDÊNCIA RECENTE (falas do usuário, ultra-curto): {evidence or '—'}"
    return "\n\n".join([
        persona_text, prefs_line, length, sensory, nsfw_hint, rules,
        f"MEMÓRIA (canon curto): {rolling_summary}",
        f"ENTIDADES: {entities_line}", f"CONTINUIDADE: {continuity}",
        evidence_block, safety
    ])

# ==== Robustez da chamada ====
def _looks_like_cloudflare_5xx(err_text: str) -> bool:
    if not err_text: return False
    s = err_text.lower()
    return ("cloudflare" in s) and any(code in s for code in ["500","502","503","504"])

def _robust_chat_call(
    model: str, messages: List[Dict[str, str]], *,
    max_tokens: int = 1536, temperature: float = 0.7, top_p: float = 0.95,
    fallback_models: Optional[List[str]] = None, tools: Optional[List[Dict]] = None
) -> Tuple[Dict, str, str]:
    attempts, last_err = 3, ""
    for i in range(attempts):
        try:
            payload = {
                "model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "top_p": top_p
            }
            if tools: payload["tools"] = tools
            if st.session_state.get("json_mode_on", False):
                payload["response_format"] = {"type": "json_object"}
            adapter_id = (st.session_state.get("together_lora_id") or "").strip()
            if adapter_id and (model or "").startswith("together/"):
                payload["adapter_id"] = adapter_id
            return route_chat_strict(model, payload)
        except Exception as e:
            last_err = str(e)
            if _looks_like_cloudflare_5xx(last_err) or "OpenRouter 502" in last_err:
                time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4)); continue
            break
    if fallback_models:
        for fb in fallback_models:
            try:
                payload_fb = {
                    "model": fb, "messages": messages, "max_tokens": max_tokens,
                    "temperature": temperature, "top_p": top_p
                }
                if tools: payload_fb["tools"] = tools
                if st.session_state.get("json_mode_on", False):
                    payload_fb["response_format"] = {"type": "json_object"}
                adapter_id = (st.session_state.get("together_lora_id") or "").strip()
                if adapter_id and (fb or "").startswith("together/"):
                    payload_fb["adapter_id"] = adapter_id
                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)
    synthetic = {"choices":[{"message":{"content":
        "Conexão oscilou. Me diga em 1 linha o próximo passo e eu continuo do ponto exato."}}]}
    return synthetic, model, "synthetic-fallback"

# ==== ENTIDADES – linha compacta ====
_ENTITY_KEYS = ("realm","portal","alias","contact","ig")
def _entities_to_line(f: Dict) -> str:
    parts = []
    for k in _ENTITY_KEYS:
        v = str(f.get(f"nerith.entity.{k}", "") or "").strip()
        if v: parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else "—"

# ==== Aviso visual de poda/resumo ====
def _mem_drop_warn(report: dict) -> None:
    if not report: return
    summarized = int(report.get("summarized_pairs", 0))
    trimmed    = int(report.get("trimmed_pairs", 0))
    hist_tokens = int(report.get("hist_tokens", 0))
    hist_budget = int(report.get("hist_budget", 0))
    if summarized or trimmed:
        msg = []
        if summarized: msg.append(f"**{summarized}** turnos antigos **resumidos**")
        if trimmed:    msg.append(f"**{trimmed}** pares verbatim **podados**")
        txt = " e ".join(msg)
        st.info(f"⚠️ Memória ajustada: {txt}. (histórico: {hist_tokens}/{hist_budget} tokens).", icon="⚠️")

# ==== Tools (opcional) ====
TOOLS = [
    {"type": "function", "function": {
        "name": "get_memory_pin",
        "description": "Retorna fatos canônicos curtos e entidades salvas (linha compacta) para Nerith.",
        "parameters": {"type": "object","properties":{},"required":[]}
    }},
    {"type": "function", "function": {
        "name": "set_fact",
        "description": "Salva/atualiza um fato canônico (chave/valor) para Nerith.",
        "parameters": {"type":"object","properties":{
            "key":{"type":"string"},"value":{"type":"string"}}, "required":["key","value"]}
    }},
    {"type": "function", "function": {
        "name": "save_event",
        "description": "Salva um evento canônico de Nerith (nerith.evento.*).",
        "parameters": {"type":"object","properties":{
            "label":{"type":"string"},"content":{"type":"string"}}, "required":[]}
    }},
    {"type": "function", "function": {
        "name": "recall_memory",
        "description": "Recupera memória longa por palavra-chave (ou a última memória) e injeta no próximo turno.",
        "parameters": {"type":"object","properties":{
            "keyword":{"type":"string","description":"Palavra-chave; use __LAST__ para última memória."}
        }}
    }},
]

# ==== User key ====
def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return f"{uid}::nerith" if uid else "anon::nerith"

# ============ SERVIÇO PRINCIPAL ============
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ===== Evidência concisa =====
    def _compact_user_evidence(self, docs: List[Dict], max_chars: int = 320) -> str:
        snippets: List[str] = []
        for d in reversed(docs):
            u = (d.get("mensagem_usuario") or "").strip()
            if u:
                u = re.sub(r"\s+", " ", u)
                snippets.append(u)
            if len(snippets) >= 4: break
        return (" | ".join(reversed(snippets)))[:max_chars]

    # ===== Recall de Memória por palavra-chave =====
    _RECALL_PATTERNS = [
        re.compile(r"\b(lembra|lembre|recorda|recorde)\s+(?:da|de|essa|dessa|aquela)?\s*(?:mem[óo]ria|cena|aventura)?\s*(?:da|de|do|sobre)?\s*\"?([^\"]+?)\"?\s*$", re.I),
        re.compile(r"\b(puxa|recupera|resgata|traz)\s+(?:a|essa|dessa|aquela)?\s*(?:mem[óo]ria|cena|aventura)?\s*(?:da|de|do|sobre)?\s*\"?([^\"]+?)\"?\s*$", re.I),
    ]

    def _extract_recall_query(self, texto: str) -> Optional[str]:
        t = (texto or "").strip()
        if not t:
            return None
        for pat in self._RECALL_PATTERNS:
            m = pat.search(t)
            if m:
                key = (m.group(2) if (m.lastindex and m.lastindex >= 2) else m.group(1)).strip()
                key = re.sub(r"^(mem[óo]ria|cena|aventura)\s*(da|de|do|sobre)?\s*", "", key, flags=re.I)
                return key[:120] if key else None
        if re.search(r"\b(lembra|lembre).*(disso|[úu]ltima mem[óo]ria)\b", t, re.I):
            return "__LAST__"
        return None

    def _recall_lore_text(self, usuario_key: str, keyword: str) -> str:
        """
        Busca memória longa por palavra-chave e retorna texto para injetar como [LORE:RECALL].
        keyword="__LAST__" usa a última memória salva nesta sessão; senão, busca semântica (lore_topk).
        """
        if keyword == "__LAST__":
            last_val = (st.session_state.get("last_saved_nerith_event_val", "") or "").strip()
            if last_val:
                return last_val[:1500]
        try:
            top = lore_topk(usuario_key, keyword, k=4, allow_tags=None)
            blocos = []
            for d in top or []:
                txt = (d.get("texto", "") or "").strip()
                if txt:
                    blocos.append(txt)
            lore_text = " | ".join(blocos)
            return lore_text[:1500]
        except Exception:
            return ""

    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        usuario_key = _current_user_key()

        persona_text, history_boot = self._load_persona()

        # reset/colar boot via sidebar
        reset_flag = bool(st.session_state.get("reset_persona", False)
                          or st.session_state.get("force_boot", False))

        # facts/prefs
        try: f_all = cached_get_facts(usuario_key) or {}
        except Exception: f_all = {}
        prefs = _read_prefs(f_all)

        # local/momento
        local_atual = self._safe_get_local(usuario_key)

        # PIN canônico
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # foco sensorial (rotativo leve)
        pool = ["calor da pele","brilho azul","respiração","perfume","toque das mãos","timbre da voz"]
        idx = (int(st.session_state.get("nerith_attr_idx", -1)) + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        foco = pool[idx]

        # NSFW nuance
        try: nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception: nsfw_on = False
        nivel = prefs.get("nivel_sensual","sutil")
        if not nsfw_on:
            nsfw_hint = ("NSFW: BLOQUEADO. Use sugestão e tensão sem descrição explícita; foque em atmosfera e condução.")
        else:
            nsfw_hint = {
                "sutil": "NSFW: LIBERADO. Insinuação elegante; foco sensorial; sem gráficos excessivos.",
                "alta":  "NSFW: LIBERADO. Intensidade elevada com detalhes sensoriais quando solicitado.",
            }.get(nivel, "NSFW: LIBERADO. Sensualidade progressiva com coerência de cena.")

        # resumo + entidades + evidência recente
        rolling = self._get_rolling_summary(usuario_key)
        entities_line = _entities_to_line(f_all)
        try: docs = cached_get_history(usuario_key) or []
        except Exception: docs = []
        evidence = self._compact_user_evidence(docs, max_chars=320)

        # ===== Recall automático com base no prompt (opcional)
        kw_auto = self._extract_recall_query(prompt)
        if kw_auto:
            st.session_state["nerith_recall_inject"] = self._recall_lore_text(usuario_key, kw_auto)

        # system único
        system_block = _build_system_block(
            persona_text=persona_text, rolling_summary=rolling, sensory_focus=foco,
            nsfw_hint=nsfw_hint, scene_loc=local_atual or "—", entities_line=entities_line,
            evidence=evidence, prefs_line=_prefs_line(prefs),
            scene_time=st.session_state.get("momento_atual","")
        )

        # ===== Recall acionado via sidebar/tool (injeta antes do LORE automático)
        recall_text = (st.session_state.pop("nerith_recall_inject", "") or "").strip()

        # LORE (memória longa)
        lore_msgs: List[Dict[str, str]] = []
        if recall_text:
            lore_msgs.append({"role": "system", "content": f"[LORE:RECALL]\n{recall_text}"})
        try:
            q = (prompt or "") + "\n" + (rolling or "")
            top = lore_topk(usuario_key, q, k=4, allow_tags=None)
            if top:
                lore_text = " | ".join(d.get("texto","") for d in top if d.get("texto"))
                if lore_text: lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception: pass

        # histórico com orçamento + boot
        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 10))
        hist_msgs = self._montar_historico(usuario_key, history_boot, model,
                                           verbatim_ultimos=verbatim_ultimos, reset_flag=reset_flag)

        # se for primeiro turno/reset e sem prompt → retorna boot
        if not prompt and hist_msgs and hist_msgs[0].get("role") == "assistant":
            boot_text = hist_msgs[0].get("content","")
            try:
                if reset_flag: save_interaction(usuario_key, "", boot_text, "system:boot:nerith")
            except Exception: pass
            return boot_text

        # guarda de local (não mudar tempo/lugar)
        local_guard = {"role":"system","content":
                       f"LOCAL_ATUAL: {local_atual or '—'}. Regra: NÃO mude tempo/lugar sem pedido explícito."}

        messages: List[Dict] = (
            [{"role":"system","content": system_block}]
            + ([{"role":"system","content": memoria_pin}] if memoria_pin else [])
            + lore_msgs
            + [local_guard]
            + hist_msgs
            + [{"role":"user","content": prompt or "…"}]
        )

        # aviso de poda/resumo
        try: _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception: pass

        # orçamento saída
        win = _get_window_for(model)
        try: prompt_tokens = sum(toklen(m.get("content","")) for m in messages if m.get("content"))
        except Exception: prompt_tokens = 0
        base_out = _safe_max_output(win, prompt_tokens)
        size = prefs.get("tamanho_resposta","media")
        mult = 1.0 if size=="media" else (0.75 if size=="curta" else 1.25)
        max_out = max(512, int(base_out * mult))
        temperature = 0.6 if prefs.get("ritmo","lento")=="lento" else (0.9 if prefs.get("ritmo")=="rapido" else 0.7)

        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]
        tools_to_use = TOOLS if st.session_state.get("tool_calling_on", False) else None

        # loop de tool-calling (até 3)
        texto, tool_calls = "", []
        for iteration in range(1, 4):
            data, used_model, provider = _robust_chat_call(
                model, messages, max_tokens=max_out, temperature=temperature, top_p=0.95,
                fallback_models=fallbacks, tools=tools_to_use
            )
            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content","") or "").strip()
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls or not tools_to_use:
                break

            st.caption(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")
            messages.append({"role":"assistant","content": texto or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}")
                func = tc.get("function", {}) or {}
                fname = func.get("name",""); args_raw = func.get("arguments","{}")
                try:
                    args = json.loads(args_raw) if args_raw else {}
                    result = self._exec_tool_call(fname, args, usuario_key)
                    messages.append({"role":"tool","tool_call_id": tool_id, "content": result})
                    st.caption(f"  ✓ {fname}: {result[:50]}...")
                except Exception as e:
                    err = f"ERRO ao executar {fname}: {str(e)}"
                    messages.append({"role":"tool","tool_call_id": tool_id, "content": err})
                    st.warning(f"⚠️ {err}")

        # Ultra IA (opcional)
        try:
            if bool(st.session_state.get("ultra_ia_on", False)) and texto:
                critic_model = st.session_state.get("ultra_critic_model", model) or model
                notes = critic_review(critic_model, system_block, prompt, texto)
                texto = polish(model, system_block, prompt, texto, notes)
        except Exception: pass

        # sentinela de esquecimento
        try:
            if re.search(r"\b(n[ãa]o (lembro|recordo)|quem (é|[ée] voc[êe])|me relembre|o que est[aá]vamos)\b", texto, re.I):
                st.warning("🧠 Possível esquecimento detectado. Se quiser, peça um **recap curto**.")
        except Exception: pass

        # persistências
        try: save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception: pass

        # entidades e resumo rolante
        try: self._update_entities(usuario_key, prompt, texto)
        except Exception: pass
        try: self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception: pass

        # memória longa (lore)
        try:
            frag = f"[USER] {prompt}\n[NERITH] {texto}"
            lore_save(usuario_key, frag, tags=["nerith","chat"])
        except Exception: pass

        # placeholder leve
        try:
            st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
            st.session_state["last_assistant_message"] = texto
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        # avisos de failover
        try:
            if provider == "synthetic-fallback":
                st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")
        except Exception: pass

        # sinaliza recall ao usuário
        if recall_text and texto:
            texto = "_(Trouxe a memória que você pediu — posso continuar daí.)_\n\n" + texto

        return texto

    # ===== Internals =====
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
        try: f = cached_get_facts(usuario_key) or {}
        except Exception: f = {}
        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro: blocos.append(f"parceiro_atual={parceiro}")

        # vínculo padrão
        juntos = bool(f.get("nerith.par", True))
        blocos.append(f"par={juntos}")

        ent_line = _entities_to_line(f)
        if ent_line and ent_line != "—": blocos.append(f"entidades=({ent_line})")

        try: ev = last_event(usuario_key, "primeira_vez")
        except Exception: ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")

        mem_str = "; ".join(blocos) if blocos else "—"
        return ("MEMÓRIA_PIN: "
                f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
                "Use ENTIDADES/MEMÓRIA como fonte de verdade; se faltar, não invente.")

    def _montar_historico(
        self, usuario_key: str, history_boot: List[Dict[str, str]], model: str,
        verbatim_ultimos: int = 10, reset_flag: bool = False,
    ) -> List[Dict[str, str]]:
        win = _get_window_for(model)
        hist_budget, _, _ = _budget_slices(model)
        docs = cached_get_history(usuario_key)
        force_boot = reset_flag or bool(st.session_state.get("force_boot", False))

        # texto do boot
        boot_text = ""
        if history_boot and history_boot[0].get("role") == "assistant":
            boot_text = history_boot[0].get("content","") or ""

        # sem docs OU reset forçado → injeta boot e persiste
        if force_boot or not docs:
            msgs_boot = history_boot[:] if history_boot else []
            if boot_text and not st.session_state.get("_nerith_boot_persistido", False):
                self._persist_boot(usuario_key, boot_text)
                st.session_state.pop("force_boot", None)
                st.session_state.pop("reset_persona", None)
            st.session_state["_mem_drop_report"] = {}
            return msgs_boot

        # pares user/assistant
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_nerith") or d.get("resposta_mary") or "").strip()
            if u: pares.append({"role":"user","content":u})
            if a: pares.append({"role":"assistant","content":a})

        if not pares:
            msgs_boot = history_boot[:] if history_boot else []
            if boot_text and not st.session_state.get("_nerith_boot_persistido", False):
                self._persist_boot(usuario_key, boot_text)
            st.session_state["_mem_drop_report"] = {}
            return msgs_boot

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos  = pares[:len(pares)-len(verbatim)]

        msgs: List[Dict[str,str]] = []
        summarized_pairs = 0
        trimmed_pairs = 0

        if antigos:
            summarized_pairs = len(antigos) // 2
            bloco = "\n\n".join(m["content"] for m in antigos)
            resumo = _llm_summarize(model, bloco)
            resumo_layers = [resumo]

            def _count_total_sim(resumo_texts: List[str]) -> int:
                sim_msgs = [{"role":"system","content": f"[RESUMO-{i+1}]\n{r}"} for i, r in enumerate(resumo_texts)]
                sim_msgs += verbatim
                return sum(toklen(m["content"]) for m in sim_msgs)

            while _count_total_sim(resumo_layers) > hist_budget and len(resumo_layers) < 3:
                resumo_layers[0] = _llm_summarize(model, resumo_layers[0])

            for i, r in enumerate(resumo_layers, start=1):
                msgs.append({"role":"system","content": f"[RESUMO-{i}]\n{r}"})

        msgs.extend(verbatim)

        def _hist_tokens(mm: List[Dict]) -> int:
            return sum(toklen(m.get("content","")) for m in mm if m.get("content"))

        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]; trimmed_pairs += 1
            else:
                verbatim = []
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim

        hist_tokens = sum(toklen(m.get("content","")) for m in msgs if m.get("content"))
        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": summarized_pairs,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": hist_tokens,
            "hist_budget": hist_budget,
        }

        # injeta boot sempre no início
        return (history_boot[:] if history_boot else []) + msgs

    # ===== Rolling summary (nerith.rs.v2) =====
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("nerith.rs.v2", "") or f.get("nerith.rolling_summary","") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("nerith.rs.v2", "")
            last_update_ts = float(f.get("nerith.rs.v2.ts", 0))
            now = time.time()
            if not last_summary: return True
            if now - last_update_ts > 300: return True
            if (len(last_user) + len(last_assistant)) > 100: return True
            return False
        except Exception:
            return True

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        if not self._should_update_summary(usuario_key, last_user, last_assistant): return
        seed = ("Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos duráveis "
                "(nomes, locais/tempo atual, relação/rumo, itens/gestos fixos). Proíba diálogos.")
        try:
            data, _, _ = route_chat_strict(model, {
                "model": model,
                "messages": [{"role":"system","content": seed},
                             {"role":"user","content": f"USER:\n{last_user}\n\nNERITH:\n{last_assistant}"}],
                "max_tokens": 180, "temperature": 0.2, "top_p": 0.9
            })
            resumo = (data.get("choices",[{}])[0].get("message",{}) or {}).get("content","").strip()
            if resumo:
                set_fact(usuario_key, "nerith.rs.v2", resumo, {"fonte":"auto_summary"})
                set_fact(usuario_key, "nerith.rs.v2.ts", time.time(), {"fonte":"auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            pass

    # ===== Entidades básicas (heurística leve) =====
    _REALM_PAT  = re.compile(r"\b(elysarix|terra|mundo humano)\b", re.I)
    _PORTAL_PAT = re.compile(r"\b(portal|passagem|guarda-roupa)\b", re.I)
    _IG_PAT     = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9_.]{2,30})")

    def _update_entities(self, usuario_key: str, user_text: str, assistant_text: str) -> None:
        try: f = cached_get_facts(usuario_key) or {}
        except Exception: f = {}

        realm = self._REALM_PAT.search((user_text or "") + " " + (assistant_text or ""))
        if realm:
            cur = str(f.get("nerith.entity.realm","") or "").strip()
            val = realm.group(1).lower()
            if not cur or len(val) >= len(cur):
                set_fact(usuario_key, "nerith.entity.realm", val, {"fonte":"extracted"}); clear_user_cache(usuario_key)

        if self._PORTAL_PAT.search((user_text or "") + " " + (assistant_text or "")):
            cur = str(f.get("nerith.entity.portal","") or "").strip()
            if not cur:
                set_fact(usuario_key, "nerith.entity.portal", "guarda-roupa", {"fonte":"extracted"}); clear_user_cache(usuario_key)

        ig = self._IG_PAT.search(user_text or "") or self._IG_PAT.search(assistant_text or "")
        if ig:
            handle = ig.group(1).strip("@")
            cur = str(f.get("nerith.entity.ig","") or "").strip()
            if not cur:
                set_fact(usuario_key, "nerith.entity.ig", "@"+handle, {"fonte":"extracted"}); clear_user_cache(usuario_key)

    # ===== Placeholder leve =====
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s: return "Continua do ponto exato… conduz."
        if any(k in s for k in ["vamos","topa","prefere","quer"]): return "Quero — mantém o cenário e conduz."
        if scene_loc: return f"No {scene_loc} mesmo — aproxima e conduz."
        return "Mantém o cenário e escala com calma."

    # ===== Evidência concisa =====
    def _compact_user_evidence(self, docs: List[Dict], max_chars: int = 320) -> str:
        snippets: List[str] = []
        for d in reversed(docs):
            u = (d.get("mensagem_usuario") or "").strip()
            if u:
                u = re.sub(r"\s+", " ", u)
                snippets.append(u)
            if len(snippets) >= 4: break
        return (" | ".join(reversed(snippets)))[:max_chars]

    # ===== Persistência do boot (colar na hora) =====
    def _persist_boot(self, usuario_key: str, boot_text: str) -> None:
        if not (usuario_key and boot_text): return
        try: save_interaction(usuario_key, "", boot_text, "system:boot:nerith")
        except Exception: pass
        try:
            docs = _cache_history.get(usuario_key) or []
            docs = list(docs)
            docs.append({
                "mensagem_usuario": "", "resposta_nerith": boot_text,
                "provider_model": "system:boot:nerith", "ts": time.time(),
            })
            _cache_history[usuario_key] = docs; _cache_ts[f"hist_{usuario_key}"] = time.time()
        except Exception: pass
        st.session_state["_nerith_boot_persistido"] = True

    # ===== Execução de ferramentas =====
    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        try:
            if name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, st.session_state.get("user_id","") or "")
            if name == "set_fact":
                k = (args or {}).get("key",""); v = (args or {}).get("value","")
                if not k: return "ERRO: key ausente."
                set_fact(usuario_key, k, v, {"fonte":"tool_call"}); clear_user_cache(usuario_key)
                return f"OK: {k}={v}"
            if name == "save_event":
                label = (args or {}).get("label","").strip()
                content = (args or {}).get("content","").strip()
                if not content: content = st.session_state.get("last_assistant_message","").strip()
                if not content: return "ERRO: nenhum conteúdo para salvar."
                if not label:
                    low = content.lower()
                    label = "elysarix" if "elysarix" in low else f"evento_{int(time.time())}"
                fact_key = f"nerith.evento.{label}"
                set_fact(usuario_key, fact_key, content, {"fonte":"tool_call"}); clear_user_cache(usuario_key)
                st.session_state["last_saved_nerith_event_key"] = fact_key
                st.session_state["last_saved_nerith_event_val"] = content
                return f"OK: salvo em {fact_key}"
            if name == "recall_memory":
                kw = (args or {}).get("keyword","").strip() or "__LAST__"
                txt = self._recall_lore_text(usuario_key, kw)
                if not txt:
                    return "ERRO: nenhuma memória encontrada para a palavra-chave."
                st.session_state["nerith_recall_inject"] = txt
                return f"OK: memória recuperada ({len(txt)} chars)"
            return "ERRO: ferramenta desconhecida"
        except Exception as e:
            return f"ERRO: {e}"

    # ===== Sidebar (atualizada com Painel de Caça) =====
    def render_sidebar(self, container) -> None:
    container.markdown(
        "**Nerith — Elfa caçadora de elfos condenados** • Disfarce humano, foco em missão. "
        "Regra: não mudar tempo/lugar sem pedido explícito."
    )

    usuario_key = _current_user_key()

    # ===== Estado de Missão (session_state) =====
    ms = st.session_state.setdefault("nerith_missao", {
        "modo": "capturar",   # ou "eliminar"
        "ultimo_pulso": "",   # ex: "forte @ 19:42"
        "suspeitos": [],      # lista: {"nome": "...", "assinatura": "...", "risco": "baixo/médio/alto"}
        "local_isolado": "",  # ex: "beco"
        "andamento": "ocioso" # "ocioso" | "varrendo" | "em_contato" | "isolado" | "interrogando" | "concluido"
    })

    # ===== Preferências/flags usuais =====
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

    # ===== Modo da missão =====
    ms["modo"] = container.selectbox(
        "Modo da operação",
        options=["capturar", "eliminar"],
        index=0 if ms.get("modo") != "eliminar" else 1
    )

    # ===== Painel de status =====
    container.markdown("### 🎯 Status da Caça")
    colA, colB = container.columns(2)
    colA.metric("Andamento", ms.get("andamento") or "—")
    colB.metric("Último pulso", ms.get("ultimo_pulso") or "—")

    # ===== Recall de memória (por palavra-chave) =====
    container.markdown("### 🧠 Memória")
    rec_col1, rec_col2 = container.columns([3,1])
    recall_kw = rec_col1.text_input(
        "Palavra-chave (ex.: 'terraço', 'floresta', 'beco')",
        value=st.session_state.get("nerith_recall_kw","")
    )
    rec_col2.write("")  # espaçamento
    btn_recall = container.button("🔎 Buscar memória", use_container_width=True)
    if btn_recall:
        st.session_state["nerith_recall_kw"] = recall_kw
        kw = (recall_kw or "").strip() or "__LAST__"
        txt = self._recall_lore_text(usuario_key, kw)   # <<< usa o helper de recall
        if txt:
            st.session_state["nerith_recall_inject"] = txt
            container.success("Memória recuperada. Será injetada na próxima resposta.")
        else:
            container.warning("Nenhuma memória encontrada para essa palavra-chave.")

    # ===== Quadrinhos (providers + botão) =====
    # >>> ADIÇÃO: providers mínimos para a função de quadrinhos
    def _scene_text_provider() -> str:
        ms_local = st.session_state.get("nerith_missao", {})
        local = (
            ms_local.get("local_isolado")
            or st.session_state.get("momento_atual")
            or "noite, beco molhado de chuva"
        )
        last_assistant = (st.session_state.get("last_assistant_message") or "")[:120]
        # descrição curta e “filmável”
        return f"{local}; two characters; tense, close-up; dynamic angle; {last_assistant}"

   # chama o botão (UI escolhe provider/modelo/tamanho)
    render_comic_button(
        get_history_docs_fn=lambda: cached_get_history(usuario_key),
        scene_text_provider=_scene_text_provider,
        title="🎞️ Quadrinho (beta)"
    )

    # <<< FIM ADIÇÃO

    suspeitos = ms.get("suspeitos") or []
    if suspeitos:
        with container.expander("Suspeitos detectados", expanded=True):
            for i, s in enumerate(suspeitos, start=1):
                container.markdown(
                    f"- **{i}. {s.get('nome','?')}** • assinatura: `{s.get('assinatura','?')}` • risco: **{s.get('risco','?')}`**"
                )
    else:
        container.caption("Nenhum suspeito ainda.")

    # ===== Botões de ação =====
    container.markdown("### 🕵️‍♀️ Ações da Operação")
    a1, a2 = container.columns(2)
    b1 = a1.button("🔎 Varrer área", use_container_width=True)
    b2 = a2.button("🚧 Isolar alvo", use_container_width=True)
    a3, a4 = container.columns(2)
    b3 = a3.button("🗝️ Extrair informação", use_container_width=True)
    b4 = a4.button("✅ Encerrar operação", use_container_width=True)

    # ===== Handlers =====
    if b1:
        self._acao_varrer_area(usuario_key)
        container.success("Área varrida: pulso arcano registrado e possível suspeito adicionado.")
    if b2:
        self._acao_isolar_alvo(usuario_key)
        container.info("Alvo isolado em zona discreta. Local da cena atualizado.")
    if b3:
        self._acao_extrair_info(usuario_key)
        container.warning("Interrogatório breve registrado nos fatos.")
    if b4:
        self._acao_encerrar(usuario_key)
        container.success("Operação concluída. Estado de missão limpo.")

    # ===== Info leve de entidades/resumo (opcional) =====
    try:
        f = cached_get_facts(usuario_key) or {}
    except Exception:
        f = {}
    ent = _entities_to_line(f)
    if ent and ent != "—":
        container.caption(f"Entidades salvas: {ent}")

    rs = (f.get("nerith.rs.v2") or "")[:200]
    if rs:
        container.caption("Resumo rolante ativo (v2).")


    # ===== Ações do Painel de Caça =====
    def _acao_varrer_area(self, usuario_key: str) -> None:
        """Simula varredura: registra pulso, insere/atualiza suspeito e marca andamento."""
        try:
            ms = st.session_state.get("nerith_missao", {})
            hhmm = time.strftime("%H:%M")
            # Pulso e possível assinatura arcana (determinística leve)
            seed = str(int(time.time()))[-4:]
            assinatura = f"Σ-{seed}"
            riscos = ["baixo", "médio", "alto"]
            risco = random.choice(riscos)
            nome = random.choice(["Darian", "Seliane", "Kael", "Mirel", "Vorin", "Althaea"])

            # Atualiza painel
            ms["ultimo_pulso"] = f"forte @ {hhmm}"
            ms["andamento"] = "varrendo"

            # Adiciona/atualiza suspeito principal (evita duplicar mesma assinatura)
            lista = ms.get("suspeitos") or []
            if not any(s.get("assinatura") == assinatura for s in lista):
                lista.append({"nome": nome, "assinatura": assinatura, "risco": risco})
            ms["suspeitos"] = lista
            st.session_state["nerith_missao"] = ms

            # Persiste fatos úteis
            set_fact(usuario_key, "nerith.hunt.last_pulse", ms["ultimo_pulso"], {"fonte": "scanner"})
            set_fact(usuario_key, "nerith.hunt.last_sig", assinatura, {"fonte": "scanner"})
            set_fact(usuario_key, "nerith.hunt.status", "varrendo", {"fonte": "scanner"})
            clear_user_cache(usuario_key)
        except Exception:
            pass

    def _acao_isolar_alvo(self, usuario_key: str) -> None:
        """Escolhe um suspeito e define um local discreto para a cena, atualizando local_cena_atual."""
        try:
            ms = st.session_state.get("nerith_missao", {})
            candidatos = ms.get("suspeitos") or []
            if not candidatos:
                # cria um alvo mínimo para não falhar UX
                candidatos = [{"nome": "Alvo anônimo", "assinatura": "Σ-0000", "risco": "médio"}]
                ms["suspeitos"] = candidatos

            alvo = sorted(
                candidatos,
                key=lambda s: {"baixo":0,"médio":1,"alto":2}.get(s.get("risco","médio"),1)
            )[-1]

            locais = ["beco lateral", "terraço vazio", "subsolo do estacionamento", "galpão abandonado", "escadaria de serviço"]
            local = random.choice(locais)
            ms["local_isolado"] = local
            ms["andamento"] = "isolado"
            st.session_state["nerith_missao"] = ms

            # Atualiza 'local_cena_atual' usado pelo service para continuidade
            set_fact(usuario_key, "local_cena_atual", local, {"fonte": "isolamento"})
            # Portal não é aberto aqui; cena é no mundo humano
            set_fact(usuario_key, "portal_aberto", False, {"fonte": "isolamento"})
            # Marcar alvo atual (nome+assinatura) para referência do modelo
            set_fact(usuario_key, "nerith.hunt.target", f"{alvo.get('nome')}|{alvo.get('assinatura')}", {"fonte": "isolamento"})
            set_fact(usuario_key, "nerith.hunt.status", "isolado", {"fonte": "isolamento"})
            clear_user_cache(usuario_key)
        except Exception:
            pass

    def _acao_extrair_info(self, usuario_key: str) -> None:
        """Grava um evento de interrogatório curto e atualiza andamento."""
        try:
            ms = st.session_state.get("nerith_missao", {})
            local = ms.get("local_isolado") or (get_fact(usuario_key, "local_cena_atual", "") or "—")
            alvo = get_fact(usuario_key, "nerith.hunt.target", "") or "desconhecido"
            carimbo = time.strftime("%Y-%m-%d %H:%M:%S")

            texto_evento = (
                f"[INTERROGATORIO] alvo={alvo}; local={local}; ts={carimbo}. "
                "Nerith pressiona com presença e toques calculados; alvo confessa rotas de passagem e contatos no cais."
            )

            # Salva como fato/“evento”
            set_fact(usuario_key, f"nerith.evento.interrogatorio_{int(time.time())}", texto_evento, {"fonte": "missao"})
            set_fact(usuario_key, "nerith.hunt.status", "interrogando", {"fonte": "missao"})
            ms["andamento"] = "interrogando"
            st.session_state["nerith_missao"] = ms
            clear_user_cache(usuario_key)
        except Exception:
            pass

    def _acao_encerrar(self, usuario_key: str) -> None:
        """Finaliza a operação e limpa estado transitório da missão."""
        try:
            ms_default = {
                "modo": "capturar",
                "ultimo_pulso": "",
                "suspeitos": [],
                "local_isolado": "",
                "andamento": "ocioso"
            }
            st.session_state["nerith_missao"] = ms_default

            set_fact(usuario_key, "nerith.hunt.status", "concluido", {"fonte": "missao"})
            # Mantém local_cena_atual como está; não fecha portal aqui.
            clear_user_cache(usuario_key)
        except Exception:
            pass
