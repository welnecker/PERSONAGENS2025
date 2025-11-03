# characters/nerith/service.py
# NerithService — versão natural, com continuidade estável e resposta orgânica a perguntas.
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import List, Dict, Tuple, Optional

import streamlit as st

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, set_fact, last_event
)
from core.tokens import toklen


# =========================
# Chave estável do usuário
# =========================
def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return f"{uid or 'anon'}::nerith"


# =========================
# NSFW (opcional)
# =========================
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False


# =========================
# Persona
# =========================
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é NERITH — elfa de pele azul cobalto, confiante e dominante no charme, carinhosa quando quer. "
            "Fale em primeira pessoa (eu). Integre 1–2 pistas sensoriais à ação (sem listas). "
            "Mantenha continuidade rigorosa de cenário/tempo/decisões. 4–6 parágrafos; 2–4 frases cada."
        )
        return txt, []


# =========================
# Cache leve (facts/history)
# =========================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos

_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_timestamps: Dict[str, float] = {}


def _purge_expired_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_timestamps.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None)
            _cache_timestamps.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_timestamps.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None)
            _cache_timestamps.pop(f"hist_{k}", None)


def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None)
    _cache_timestamps.pop(f"facts_{user_key}", None)
    for ck in list(_cache_history.keys()):
        if ck.startswith(user_key):
            _cache_history.pop(ck, None)
            _cache_timestamps.pop(f"hist_{ck}", None)


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


def _extract_ts(d: Dict) -> Optional[float]:
    for k in ("ts", "timestamp", "created_at", "updated_at", "date"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "")).timestamp()
            except Exception:
                pass
    return None


def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    cache_key = f"{user_key}"
    if cache_key in _cache_history and (now - _cache_timestamps.get(f"hist_{cache_key}", 0) < CACHE_TTL):
        docs = _cache_history[cache_key]
    else:
        try:
            docs = get_history_docs(user_key) or []
        except Exception:
            docs = []
        _cache_history[cache_key] = docs
        _cache_timestamps[f"hist_{cache_key}"] = now

    if docs:
        ts0 = _extract_ts(docs[0])
        tsN = _extract_ts(docs[-1])
        if ts0 is not None and tsN is not None and ts0 > tsN:
            docs = list(reversed(docs))
        elif ts0 is None and tsN is None:
            docs = list(reversed(docs))
    return docs


# =========================
# Heurísticas suaves
# =========================
_Q_PAT = re.compile(r"([^?¡!.\n]{1,300}\?)")


def _extract_questions(txt: str, max_q: int = 2) -> list[str]:
    if not txt:
        return []
    qs = [m.group(1).strip() for m in _Q_PAT.finditer(txt)]
    low = txt.lower()
    # perguntas sem "?" explícito (sim/não)
    if any(w in low for w in ["está disposta", "topa", "aceita", "vamos", "podemos", "quer"]):
        qs.append("Você topa/está disposta?")
    uniq = []
    for q in qs:
        if q and q not in uniq:
            uniq.append(q)
        if len(uniq) >= max_q:
            break
    return uniq


def _recent_user_questions(usuario_key: str) -> list[str]:
    docs = cached_get_history(usuario_key) or []
    buf = []
    for d in reversed(docs[-6:]):  # últimas até ~6 falas
        u = (d.get("mensagem_usuario") or d.get("user_message") or d.get("user") or
             d.get("input") or d.get("prompt") or "")
        if u:
            buf.append(u.strip())
    return _extract_questions(" ".join(buf), max_q=2)


# =========================
# Serviço
# =========================
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ---------- API ----------
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        persona_text, history_boot = self._load_persona()
        usuario_key = _current_user_key()

        # ===== Cena em sessão (não persistente) =====
        st.session_state.setdefault("nerith_scene_id", int(time.time()))
        st.session_state.setdefault("nerith_ferrao_used_scene", False)
        st.session_state.setdefault("nerith_location_lock", "")  # "", "quarto", "elysarix"

        # ===== Boot sem prompt: respeita último estado =====
        last_docs = cached_get_history(usuario_key)
        if not prompt:
            if last_docs:
                # devolve a última fala da Nerith para manter continuidade visual
                for d in reversed(last_docs):
                    a = (d.get("resposta_nerith") or d.get("assistant_message") or
                         d.get("assistant") or d.get("response") or "")
                    if a:
                        return a.strip()
            # inicia cena sutil (sem teleporte)
            facts0 = cached_get_facts(usuario_key)
            local0 = (facts0.get("local_cena_atual") or "quarto").strip()
            boot = "A porta range discretamente. Eu já estava aqui, encostada na madeira quente do guarda-roupa, esperando você me chamar."
            set_fact(usuario_key, "local_cena_atual", local0, {"fonte": "boot"})
            clear_user_cache(usuario_key)
            save_interaction(usuario_key, "", boot, "system:boot")
            return boot

        # ===== Fatos base =====
        fatos = cached_get_facts(usuario_key)
        local_atual = (fatos.get("local_cena_atual") or "quarto").strip()
        portal_aberto = str(fatos.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
        gravidez_elysarix = bool(fatos.get("gravidez_elysarix", False))

        # ===== Trava de cenário: só muda com pedido claro =====
        user_location_cmd = self._check_user_location_command(prompt)
        if user_location_cmd:
            local_atual = user_location_cmd
            set_fact(usuario_key, "local_cena_atual", local_atual, {"fonte": "user_command"})
            if local_atual.lower() == "elysarix":
                set_fact(usuario_key, "portal_aberto", "True", {"fonte": "auto"})
            clear_user_cache(usuario_key)

        # Se o portal estiver marcado e já estivermos em Elysarix, não “volta sozinho”
        if portal_aberto and local_atual.lower() != "elysarix":
            local_atual = "Elysarix"
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "coerencia_portal"})
            clear_user_cache(usuario_key)

        # ===== Sensory + NSFW =====
        foco = self._next_sensory_focus()
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual, linguagem elegante (sem tecnicismos frios)."
            if nsfw_on else
            "NSFW: BLOQUEADO. Flerte e intimidade sugerida; sem ato explícito."
        )

        # ===== Perguntas orgânicas (1º parágrafo) =====
        q_now = _extract_questions(prompt, max_q=2)
        if not q_now:
            q_now = _recent_user_questions(usuario_key)

        # ===== System leve (sem listas, sem rigidez) =====
        system_parts = [
            persona_text,
            f"SENSORIAL: traga 1–2 pistas envolvendo {foco} ainda no início, integradas à ação.",
            nsfw_hint,
            (
                "CONTINUIDADE: não mude cenário/tempo/decisões sem pedido explícito. "
                "Se local_cena_atual=Elysarix, permaneça; se 'quarto', permaneça. "
                "Só atravesse portal se o usuário pedir claramente."
            ),
            (
                "FERRÃO: só pode entrar em cena se houver consentimento explícito no turno atual "
                "e se ainda **não** foi usado nesta cena (cooldown por cena)."
            ),
            "ESTILO: 4–6 parágrafos, 2–4 frases por parágrafo, sem listas numeradas, sem metacomentário.",
        ]
        if gravidez_elysarix:
            system_parts.append("ELYSARIX: gravidez ativa registrada; trate com cuidado a decisão de mundos.")

        if q_now:
            system_parts.append(
                f"PERGUNTA_DO_USUÁRIO: responda naturalmente no primeiro parágrafo a: { ' | '.join(q_now) }."
            )

        system_block = "\n".join(system_parts)

        # ===== Histórico enxuto (sem resumos rígidos) =====
        hist_msgs = self._montar_historico(usuario_key, history_boot)

        # ===== Montagem de mensagens =====
        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": system_block}]
            + [{"role": "system", "content": self._build_memory_pin(usuario_key, st.session_state.get("user_id","") or "")}]
            + [{"role": "system", "content": f"LOCAL_ATUAL={local_atual} — mantenha este cenário a menos que o usuário peça trocar."}]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # ===== Chamada robusta =====
        data, used_model, provider = self._robust_chat_call(model, {
            "model": model,
            "messages": messages,
            "max_tokens": 1536,
            "temperature": 0.7,
            "top_p": 0.95,
            "response_format": {"type": "json_object"} if st.session_state.get("json_mode_on", False) else None
        })

        msg = (data.get("choices", [{}])[0].get("message", {}) or {})
        texto = (msg.get("content", "") or "").strip()

        # ===== Coerências pós-geração =====
        texto = self._post_fix_scene(usuario_key, local_atual, texto)
        texto = self._post_fix_ferrao_cooldown(texto)

        # ===== Persistências =====
        try:
            save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass
        try:
            self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception:
            pass
        try:
            st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
            st.session_state["last_assistant_message"] = texto
        except Exception:
            pass

        return texto

    # ---------- Infra ----------
    def _robust_chat_call(self, model: str, payload: Dict):
        attempts = 3
        last_err = ""
        for i in range(attempts):
            try:
                clean = {k: v for k, v in payload.items() if v is not None}
                return route_chat_strict(model, clean)
            except Exception as e:
                last_err = str(e)
                time.sleep((0.7 * (2 ** i)))
        raise RuntimeError(last_err)

    # ---------- Histórico ----------
    def _montar_historico(self, usuario_key: str, history_boot: List[Dict[str, str]]) -> List[Dict[str, str]]:
        docs = cached_get_history(usuario_key)
        if not docs:
            return history_boot[:] if history_boot else []
        pares: List[Dict[str, str]] = []
        for d in docs[-20:]:  # últimos ~20 eventos verbatim (simples e eficaz)
            u = (d.get("mensagem_usuario") or d.get("user_message") or d.get("user") or
                 d.get("input") or d.get("prompt") or "")
            a = (d.get("resposta_nerith") or d.get("assistant_message") or
                 d.get("assistant") or d.get("response") or d.get("output") or "")
            if u and u.strip():
                pares.append({"role": "user", "content": u.strip()})
            if a and a.strip():
                pares.append({"role": "assistant", "content": a.strip()})
        return pares if pares else (history_boot[:] if history_boot else [])

    # ---------- Resumo rolante ----------
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("nerith.rs.v2", "") or f.get("nerith.rolling_summary", "") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("nerith.rs.v2", "")
            last_update_ts = float(f.get("nerith.rs.v2.ts", 0))
            now = time.time()
            if not last_summary:
                return True
            if now - last_update_ts > 300:
                return True
            if (len(last_user) + len(last_assistant)) > 120:
                return True
            return False
        except Exception:
            return True

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        if not self._should_update_summary(usuario_key, last_user, last_assistant):
            return
        seed = (
            "Resuma a conversa recente em 6–10 frases telegráficas, apenas fatos duráveis "
            "(nomes, relação, local/tempo atual, itens/gestos fixos, rumo do enredo). Proíba diálogos literais."
        )
        try:
            data, _, _ = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": f"USER:\n{last_user}\n\nNERITH:\n{last_assistant}"}
                ],
                "max_tokens": 180,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            resumo = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
            if resumo:
                set_fact(usuario_key, "nerith.rs.v2", resumo, {"fonte": "auto_summary"})
                set_fact(usuario_key, "nerith.rs.v2.ts", time.time(), {"fonte": "auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            pass

    # ---------- Continuidade & coerências ----------
    def _post_fix_scene(self, usuario_key: str, local_atual: str, texto: str) -> str:
        """
        Garante que a resposta não mude de cenário sem pedido explícito.
        Se detectar descrição que contradiz o local, ajusta para 'lembrança' ou 'promessa',
        sem teleporte real.
        """
        if not texto:
            return texto
        low = texto.lower()
        local = (local_atual or "").lower()

        # Sinais fortes de Elysarix
        ely_markers = ["elysarix", "duas luas", "floresta de cristal", "relva de cristal", "mar de cristal"]
        # Sinais do quarto/mundo humano
        quarto_markers = ["quarto", "guarda-roupa", "lençol", "travesseiro", "apartamento", "casa"]

        mudou_para_ely = any(m in low for m in ely_markers)
        mudou_para_quarto = any(m in low for m in quarto_markers)

        if local == "elysarix" and mudou_para_quarto:
            # converte para lembrança/flash ou planejamento (sem sair de Elysarix)
            texto = re.sub(r"(?i)\b(quarto|guarda-roupa|lençol|travesseiro|apartamento|casa)\b",
                           "a lembrança do quarto distante", texto, count=1)
        elif local == "quarto" and mudou_para_ely:
            # sem pedido claro, trate como imaginação/projeção
            if "portal" not in low and "atravess" not in low and "vamos para elysarix" not in low:
                texto = re.sub(r"(?i)\b(elysarix|duas luas|floresta de cristal|relva de cristal|mar de cristal)\b",
                               "a promessa de Elysarix que ainda vamos atravessar", texto, count=1)

        return texto

    def _post_fix_ferrao_cooldown(self, texto: str) -> str:
        """
        Evita reaparecimento do ferrão na mesma cena.
        Se já foi usado em cena, suaviza referência para carícia/tendril sem ferrão.
        """
        if not texto:
            return texto
        used = bool(st.session_state.get("nerith_ferrao_used_scene", False))
        if used and re.search(r"(?i)\bferr[aã]o\b", texto):
            texto = re.sub(r"(?i)\bferr[aã]o\b", "tendril", texto)
        # Marca uso se houver consentimento explícito no prompt atual
        cur_user = (st.session_state.get("user_input") or st.session_state.get("chat_input") or "").lower()
        if re.search(r"(?i)\bferr[aã]o\b", texto) and any(k in cur_user for k in ["ferrão", "ferrão agora", "quero o ferrão"]):
            st.session_state["nerith_ferrao_used_scene"] = True
        return texto

    # ---------- Aux -------
    def _next_sensory_focus(self) -> str:
        pool = [
            "pele azul/temperatura", "tendrils/tato", "orelhas pontudas/vibração",
            "olhos esmeralda/contato visual", "língua tendril/beijo",
            "quadris/coxas", "perfume/doçura", "respiração/timbre"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        return pool[idx]

    def _get_user_prompt(self) -> str:
        return (
            st.session_state.get("chat_input")
            or st.session_state.get("user_input")
            or st.session_state.get("last_user_message")
            or st.session_state.get("prompt")
            or ""
        ).strip()

    def _check_user_location_command(self, prompt: str) -> Optional[str]:
        pl = (prompt or "").lower()
        if any(w in pl for w in ["vamos para elysarix", "ir para elysarix", "atravessar para elysarix", "em elysarix"]):
            return "Elysarix"
        if any(w in pl for w in ["ficar no quarto", "no quarto", "voltar ao quarto", "em casa"]):
            return "quarto"
        return None

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
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
        mundo = str(f.get("mundo_escolhido", "") or "").strip()
        if mundo:
            blocos.append(f"mundo_escolhido={mundo}")
        if "portal_aberto" in f:
            blocos.append(f"portal_aberto={f.get('portal_aberto')}")
        try:
            ev = last_event(usuario_key, "primeira_vez")
            if ev:
                ts = ev.get("ts")
                quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
                blocos.append(f"primeira_vez@{quando}")
        except Exception:
            pass
        mem_str = "; ".join(blocos) if blocos else "—"
        return (
            "MEMÓRIA_PIN: "
            f"NOME_USUÁRIO={nome_usuario}. FATOS={{ {mem_str} }}. "
            "Regras: continuidade estrita de cenário e decisões; não invente entidades específicas."
        )

    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Fala comigo — quer ficar aqui ou atravessar?"
        if any(k in s for k in ["vamos", "topa", "prefere"]):
            return "Topo. Mantém o ritmo…"
        if scene_loc:
            return f"No {scene_loc} mesmo. Me guia devagar."
        return "Mantém o cenário e vem mais perto."

    # ---------- Persona loader (FALTAVA) ----------
    def _load_persona(self) -> Tuple[str, List[Dict[str, str]]]:
        """Retorna texto de persona e mensagens de boot opcionais."""
        return get_persona()
