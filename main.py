# main.py
from __future__ import annotations

import os
import sys
import time
import hmac
import hashlib
import inspect
import traceback
from pathlib import Path
from typing import Optional, List, Tuple

import streamlit as st

# ---------- Path / Page ----------
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")

# ---------- Gate opcional ----------
def _check_scrypt(pwd: str) -> bool:
    cfg = st.secrets.get("auth", {})
    salt_hex = cfg.get("salt", "")
    hash_hex = cfg.get("password_scrypt", "")
    if not salt_hex or not hash_hex:
        return False
    try:
        calc = hashlib.scrypt(
            pwd.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2**14, r=8, p=1
        ).hex()
        return hmac.compare_digest(calc, hash_hex)
    except Exception:
        return False

def require_password_if_configured(app_name: str = "PERSONAGENS 2025"):
    if "auth" not in st.secrets:
        return
    st.session_state.setdefault("_auth_ok", False)
    st.session_state.setdefault("_auth_attempts", 0)
    st.session_state.setdefault("_auth_block_until", 0.0)

    now = time.time()
    if now < st.session_state["_auth_block_until"]:
        wait = int(st.session_state["_auth_block_until"] - now)
        st.error(f"Tentativas excessivas. Tente novamente em {wait}s.")
        st.stop()

    if st.session_state["_auth_ok"]:
        with st.sidebar:
            if st.button("Sair"):
                for k in ["_auth_ok", "_auth_attempts", "_auth_block_until"]:
                    st.session_state.pop(k, None)
                st.rerun()
        return

    st.title(f"🔒 {app_name}")
    pwd = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if _check_scrypt(pwd):
            st.session_state["_auth_ok"] = True
            st.session_state["_auth_attempts"] = 0
            st.success("Acesso liberado.")
            st.rerun()
        else:
            st.session_state["_auth_attempts"] += 1
            backoff = 5 * (3 ** max(0, st.session_state["_auth_attempts"] - 1))
            st.session_state["_auth_block_until"] = time.time() + backoff
            st.error("Senha incorreta.")
            st.stop()
    st.stop()

require_password_if_configured("PERSONAGENS 2025")
st.title("PERSONAGENS 2025")

# ---------- Carrega secrets → env (antes de importar wrappers/DB) ----------
def _load_env_from_secrets():
    sec = st.secrets
    mapping = {
        "OPENROUTER_API_KEY": sec.get("OPENROUTER_API_KEY", ""),
        "TOGETHER_API_KEY":   sec.get("TOGETHER_API_KEY", ""),
        "LLM_HTTP_TIMEOUT":   sec.get("LLM_HTTP_TIMEOUT", "60"),
        "MONGO_USER":         sec.get("MONGO_USER", ""),
        "MONGO_PASS":         sec.get("MONGO_PASS", ""),
        "MONGO_CLUSTER":      sec.get("MONGO_CLUSTER", ""),
        "APP_NAME":           sec.get("APP_NAME", "personagens2025"),
        "APP_PUBLIC_URL":     sec.get("APP_PUBLIC_URL", ""),
        # >>> Mongo como padrão <<<
        "DB_BACKEND":         sec.get("DB_BACKEND", "mongo"),
    }
    for k, v in mapping.items():
        if v and not os.environ.get(k):
            os.environ[k] = str(v)

_load_env_from_secrets()

# ---------- Registry de personagens ----------
try:
    from characters.registry import get_service, list_characters
except Exception as e:
    st.error("Falha ao importar `characters.registry`.\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

# ---------- Router de provedores/modelos ----------
try:
    from core.service_router import available_providers, list_models, chat as provider_chat
except Exception:
    def available_providers():
        provs = []
        provs.append(("OpenRouter", bool(os.environ.get("OPENROUTER_API_KEY")), "OK" if os.environ.get("OPENROUTER_API_KEY") else "sem chave"))
        provs.append(("Together",   bool(os.environ.get("TOGETHER_API_KEY")), "OK" if os.environ.get("TOGETHER_API_KEY") else "sem chave"))
        return provs
    def list_models(_p: str | None = None):
        return [
            "deepseek/deepseek-chat-v3-0324",
            "anthropic/claude-3.5-haiku",
            "qwen/qwen3-max",
            "nousresearch/hermes-3-llama-3.1-405b",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/Qwen/QwQ-32B",
        ]
    def provider_chat(model: str, messages: List[dict], **kw):
        raise RuntimeError("service_router indisponível.")

# ---------- Database helpers ----------
try:
    from core.database import get_backend, set_backend, ping_db, get_col, db_status
except Exception:
    def get_backend() -> str: return os.environ.get("DB_BACKEND", "memory")
    def set_backend(kind: str) -> None: os.environ["DB_BACKEND"] = kind
    def ping_db(): return ("memory", True, "memória local")
    def get_col(_name: str): raise RuntimeError("DB indisponível")
    def db_status(): return ("unknown", "core.database ausente")

# --- Força Mongo como padrão se nada tiver sido escolhido explicitamente
try:
    if os.environ.get("DB_BACKEND", "").strip() == "":
        os.environ["DB_BACKEND"] = "mongo"
    if get_backend() != "mongo":
        set_backend("mongo")
except Exception:
    pass


# Repositório (histórico/fatos) — safe fallback
try:
    from core.repositories import (
        get_history_docs, get_history_docs_multi,
        set_fact, get_fact, get_facts,
        delete_user_history, delete_last_interaction, delete_all_user_data,
        register_event, list_events,
        save_interaction,
    )
except Exception:
    def get_history_docs(_u: str, limit: int = 400): return []
    def get_history_docs_multi(_keys: List[str], limit: int = 400): return []
    def set_fact(*a, **k): ...
    def get_fact(_u: str, _k: str, default=None): return default
    def get_facts(_u: str): return {}
    def delete_user_history(_u: str): return 0
    def delete_last_interaction(_u: str): return False
    def delete_all_user_data(_u: str): return {"hist": 0, "state": 0, "eventos": 0, "perfil": 0}
    def register_event(*a, **k): ...
    def list_events(_u: str, limit: int = 5): return []
    def save_interaction(*a, **k): ...

# ---------- Sidebar: Provedores + DB ----------
st.sidebar.subheader("🧠 Provedores LLM")
for name, ok, detail in available_providers():
    st.sidebar.write(f"- **{name}**: {'✅ OK' if ok else '❌'} ({detail})")

st.sidebar.markdown("---")
st.sidebar.subheader("🗄️ Banco de Dados")

bk, info = db_status()
st.sidebar.caption(f"Backend: **{bk}** — {info}")

cur_backend = get_backend()
choice_backend = st.sidebar.radio(
    "Backend",
    options=["memory", "mongo"],
    index=(0 if cur_backend != "mongo" else 1),
    format_func=lambda x: "Memória (local)" if x == "memory" else "MongoDB (remoto)",
    horizontal=True,
)
if choice_backend != cur_backend:
    set_backend(choice_backend)
    st.sidebar.success(f"Backend ajustado para **{choice_backend}**.")
    st.rerun()

if st.sidebar.button("🔍 Testar conexão DB"):
    kind, ok, detail = ping_db()
    (st.sidebar.success if ok else st.sidebar.error)(f"{kind}: {detail}")

if choice_backend == "mongo":
    mu = os.environ.get("MONGO_USER", "")
    mc = os.environ.get("MONGO_CLUSTER", "")
    st.sidebar.caption(f"Mongo: user=`{mu or '—'}` host=`{mc or '—'}`")
    if st.sidebar.button("🧪 Insert/Find (diagnostic)"):
        try:
            col = get_col("diagnostic")
            r_id = col.insert_one({"ts": __import__("datetime").datetime.utcnow()})["inserted_id"]
            last = col.find_one(sort=[("ts", -1)])
            st.sidebar.success(f"OK (id={r_id}) — last={last}")
        except Exception as e:
            st.sidebar.error(f"Falha: {e}")

st.sidebar.markdown("---")

# ---------- Estado base ----------
st.session_state.setdefault("user_id", "Janio Donisete")
st.session_state.setdefault("character", "Mary")
all_models = list_models(None)
st.session_state.setdefault("model", (all_models[0] if all_models else "deepseek/deepseek-chat-v3-0324"))
st.session_state.setdefault("history", [])  # List[Tuple[str, str]]
st.session_state.setdefault("history_loaded_for", "")
st.session_state.setdefault("_active_key", "")  # <- para detectar troca de thread

# ---------- Controles topo ----------
c1, c2 = st.columns([2, 2])
with c1:
    st.text_input("👤 Usuário", key="user_id", placeholder="Seu nome ou identificador")
with c2:
    names = list_characters()
    default_idx = names.index("Mary") if "Mary" in names else 0
    st.selectbox("🎭 Personagem", names, index=default_idx, key="character")

st.selectbox("🧠 Modelo", list_models(None), key="model")

# ---------- Helpers de histórico ----------
def _user_keys_for_history(user_id: str, character_name: str) -> List[str]:
    """
    Retorna as chaves a consultar no histórico.
    - Para Mary: inclui chave legada (user_id).
    - Para demais personagens: SOMENTE a chave por-personagem (user_id::personagem).
    """
    ch = (character_name or "").strip().lower()
    primary = f"{user_id}::{ch}"
    if ch == "mary":
        return [primary, user_id]  # inclui legado
    return [primary]

def _reload_history(force: bool = False):
    user_id = str(st.session_state["user_id"])
    char = str(st.session_state["character"])
    # chave única da thread ativa, incluindo backend (para evitar “cache” cruzado)
    key = f"{user_id}|{char}|{get_backend()}"
    if not force and st.session_state["history_loaded_for"] == key:
        return
    try:
        keys = _user_keys_for_history(user_id, char)
        docs = get_history_docs_multi(keys, limit=400) or []
        hist: List[Tuple[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_mary") or "").strip()
            if u:
                hist.append(("user", u))
            if a:
                hist.append(("assistant", a))
        st.session_state["history"] = hist
        st.session_state["history_loaded_for"] = key
    except Exception as e:
        st.sidebar.warning(f"Não foi possível carregar o histórico: {e}")

# ---------- Troca de thread ao mudar usuário/personagem ----------
_current_active = f"{st.session_state['user_id']}::{str(st.session_state['character']).lower()}"
if st.session_state["_active_key"] != _current_active:
    st.session_state["_active_key"] = _current_active
    # limpa tela e força recarga do histórico certo
    st.session_state["history"] = []
    st.session_state["history_loaded_for"] = ""
    _reload_history(force=True)

# ---------- Instancia serviço ----------
try:
    service = get_service(st.session_state["character"])
except Exception as e:
    st.error(f"Falha ao instanciar serviço da personagem: {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

# Sidebar específico da personagem
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        st.sidebar.error(f"Erro no sidebar de {service.title}:\n\n**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
else:
    st.sidebar.caption("Sem preferências para esta personagem.")

# ---------- Sidebar: Manutenção ----------
st.sidebar.markdown("---")
st.sidebar.subheader("🧹 Manutenção")

def _force_reload_history_ui():
    st.session_state["history"] = []
    st.session_state["history_loaded_for"] = ""
    _reload_history(force=True)

_user_id = str(st.session_state.get("user_id", ""))
_char    = str(st.session_state.get("character", "")).strip().lower()
_key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id
# chave legada só para Mary
_key_legacy  = _user_id if _char == "mary" else None

colA, colB = st.sidebar.columns(2)

# Apagar último turno
if colA.button("⏪ Apagar último turno"):
    try:
        deleted = False
        try:
            deleted = delete_last_interaction(_key_primary)
        except Exception:
            pass
        if not deleted and _key_legacy:
            try:
                deleted = delete_last_interaction(_key_legacy)
            except Exception:
                pass

        if deleted:
            st.sidebar.success("Último turno apagado.")
            _force_reload_history_ui()
            st.rerun()
        else:
            st.sidebar.info("Não havia interações para apagar.")
    except Exception as e:
        st.sidebar.error(f"Falha ao apagar último turno: {e}")

# Resetar histórico
if colB.button("🔄 Resetar histórico"):
    try:
        total = 0
        try:
            total += int(delete_user_history(_key_primary) or 0)
        except Exception:
            pass
        if _key_legacy:
            try:
                total += int(delete_user_history(_key_legacy) or 0)
            except Exception:
                pass
        st.sidebar.success(f"Histórico apagado ({total} itens).")
        _force_reload_history_ui()
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Falha ao resetar histórico: {e}")

# Apagar TUDO
if st.sidebar.button("🧨 Apagar TUDO (chat + memórias)"):
    try:
        try:
            delete_all_user_data(_key_primary)
        except Exception:
            pass
        if _key_legacy:
            try:
                delete_all_user_data(_key_legacy)
            except Exception:
                pass

        st.sidebar.success("Tudo apagado para este usuário/personagem.")
        _force_reload_history_ui()
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Falha ao apagar TUDO: {e}")

# ---------- Sidebar: Memória Canônica ----------
from core.repositories import set_fact, get_facts, delete_fact  # garante import

st.sidebar.subheader("🧠 Memória Canônica")

_user_id = str(st.session_state.get("user_id", ""))
_char    = str(st.session_state.get("character", "")).strip().lower()
user_key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id

# 1) Listagem das memórias atuais
facts = {}
try:
    facts = get_facts(user_key_primary) or {}
except Exception as e:
    st.sidebar.warning(f"Não foi possível ler memórias: {e}")

if facts:
    for k, v in facts.items():
        st.sidebar.write(f"- `{k}` → {v}")
else:
    st.sidebar.caption("_Sem memórias salvas para esta personagem._")

# 2) Adicionar/atualizar memória
with st.sidebar.form("form_add_fact", clear_on_submit=True):
    st.markdown("**Adicionar/Atualizar memória**")
    f_key = st.text_input("Chave", placeholder="ex.: parceiro_atual")
    f_val = st.text_input("Valor", placeholder="ex.: Janio")
    ok = st.form_submit_button("💾 Salvar")
    if ok:
        if not f_key.strip():
            st.error("Informe a chave da memória.")
        else:
            try:
                set_fact(user_key_primary, f_key.strip(), f_val.strip(), {"fonte": "sidebar"})
                st.success("Memória salva/atualizada.")
                st.session_state["history_loaded_for"] = ""  # força recarga visual
                st.rerun()
            except Exception as e:
                st.error(f"Falha ao salvar: {e}")

# 3) Remover memória existente
if facts:
    with st.sidebar.form("form_del_fact", clear_on_submit=True):
        st.markdown("**Remover memória**")
        del_key = st.selectbox("Chave", sorted(facts.keys()))
        ok2 = st.form_submit_button("🗑️ Remover")
        if ok2 and del_key:
            try:
                delete_fact(user_key_primary, del_key)
                st.success("Memória removida.")
                st.session_state["history_loaded_for"] = ""  # força recarga
                st.rerun()
            except Exception as e:
                st.error(f"Falha ao remover: {e}")

# 4) Visão geral por personagem (diagnóstico rápido)
with st.sidebar.expander("🗂️ Memória por personagem"):
    try:
        from characters.registry import list_characters
        for name in list_characters():
            k = f"{_user_id}::{name.lower()}"
            try:
                f = get_facts(k) or {}
                st.write(f"**{name}** — {len(f)} memórias")
                # mostra só algumas entradas para não poluir
                for kk, vv in list(f.items())[:8]:
                    st.caption(f"`{kk}` → {vv}")
            except Exception:
                st.caption(f"**{name}** — erro ao ler")
    except Exception:
        st.caption("Não foi possível listar personagens.")


# ---------- Carrega histórico (primeiro render / pós-ops) ----------
_reload_history()

# ---------- Render histórico ----------
for role, content in st.session_state["history"]:
    with st.chat_message("user" if role == "user" else "assistant", avatar=("💬" if role == "user" else "💚")):
        st.markdown(content)

# ---------- LLM Ping (diagnóstico direto no provedor) ----------
with st.expander("🔧 Diagnóstico LLM"):
    if st.button("Ping modelo atual"):
        try:
            data, used, prov = provider_chat(
                st.session_state["model"],
                messages=[
                    {"role": "system", "content": "Você é um ping de diagnóstico. Responda com 'pong'."},
                    {"role": "user", "content": "diga: pong"},
                ],
                max_tokens=16,
                temperature=0.0,
                top_p=1.0,
            )
            txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
            st.success(f"{prov} • {used} → {txt!r}")
        except Exception as e:
            st.error(f"LLM ping: {e}")

# ---------- Helper de chamada segura ----------
def _safe_reply_call(_service, *, user: str, model: str, prompt: str) -> str:
    # garante fallback para services que leem prompt do session_state
    st.session_state["prompt"] = prompt

    fn = getattr(_service, "reply", None)
    if not callable(fn):
        raise RuntimeError("Service atual não expõe reply().")
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    if "prompt" in params:
        return fn(user=user, model=model, prompt=prompt)
    if params == ["user", "model"]:
        return fn(user=user, model=model)
    try:
        return fn(user, model, prompt)  # positional
    except TypeError:
        return fn(user, model)

# ---------- Chat ----------
user_prompt = st.chat_input(f"Fale com {st.session_state['character']}")
cont = st.button("🔁 Continuar", help="Prossegue a cena do ponto atual, sem mudar o local salvo.")

final_prompt: Optional[str] = None
auto_continue = False
if cont and not user_prompt:
    final_prompt = (
        "CONTINUAR: Prossiga a cena exatamente de onde a última resposta parou. "
        "Mantenha LOCAL_ATUAL, personagens presentes e tom. Não resuma; avance ação e diálogo em 1ª pessoa."
    )
    auto_continue = True
elif user_prompt:
    final_prompt = user_prompt

if final_prompt:
    with st.chat_message("user"):
        st.markdown("🔁 **Continuar**" if auto_continue else final_prompt)
    st.session_state["history"].append(("user", "🔁 Continuar" if auto_continue else final_prompt))

    with st.spinner("Gerando…"):
        try:
            text = _safe_reply_call(
                service,
                user=str(st.session_state["user_id"]),
                model=str(st.session_state["model"]),
                prompt=str(final_prompt),
            )
        except Exception as e:
            text = f"Erro durante a geração:\n\n**{e.__class__.__name__}** — {e}\n\n```\n{traceback.format_exc()}\n```"

    with st.chat_message("assistant", avatar="💚"):
        st.markdown(text)
    st.session_state["history"].append(("assistant", text))
