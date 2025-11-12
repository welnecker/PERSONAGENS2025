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
from typing import Optional, List, Tuple, Dict
import streamlit as st
import base64
import re
from pymongo import MongoClient
from datetime import datetime
import html
import importlib

import streamlit as st
from characters.registry import list_models_for_character

if st.sidebar.checkbox("🔍 DEBUG MODEL NERITH"):
    st.sidebar.write("Modelos detectados para Nerith:")
    st.sidebar.write(list_models_for_character("Nerith"))


# ========== BOOT (indexes/paths) ==========
try:
    from core.memoria_longa import ensure_indexes
    ensure_indexes()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ========== CONFIG PÁGINA ==========
st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="centered")

# ===== CSS global =====
st.markdown("""
<style>
  html, body, .stApp { overflow-x: hidden; max-width: 100vw; }
  .block-container {
    max-width: 820px; width: 100%; margin: 0 auto; box-sizing: border-box;
    padding-top: 1rem; padding-bottom: 4rem; padding-left: 16px !important; padding-right: 16px !important;
  }
  .stChatMessage { line-height: 1.5; font-size: 1.02rem; }
  .stChatFloatingInputContainer, .stChatInput { max-width: 100% !important; width: 100% !important; overflow: hidden; }
  .assistant-paragraph {
    background: rgba(59,130,246,0.18); border-left: 3px solid rgba(59,130,246,0.55);
    padding: .55rem .75rem; margin: .5rem 0; border-radius: .5rem; line-height: 1.55; color: #fff;
  }
  .assistant-paragraph a { color: #fff; text-decoration: underline; }
  .assistant-paragraph a:hover { opacity: .85; }
  .assistant-paragraph + .assistant-paragraph { margin-top: .45rem; }
  .stChatMessage ::selection { background: rgba(59,130,246,0.35); color: #fff; }
  .stMarkdown img, .stImage img, .stVideo, .stAudio { max-width: 100% !important; height: auto !important; }
  @media (max-width: 420px) {
    .block-container { padding-left: 12px !important; padding-right: 12px !important; }
    .assistant-paragraph { font-size: .98rem; }
  }
</style>
""", unsafe_allow_html=True)

# --- Plano de fundo (CSS inline) ---
try:
    IMG_DIR  # type: ignore
except NameError:
    IMG_DIR = (ROOT / "imagem")
    try:
        IMG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        IMG_DIR = Path("./imagem")
        IMG_DIR.mkdir(parents=True, exist_ok=True)

def _encode_file_b64(p: Path) -> str:
    with p.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def set_background(image_path: Path, *, darken: float = 0.25, blur_px: int = 0,
                   attach_fixed: bool = True, size_mode: str = "cover") -> None:
    if not image_path or not image_path.exists():
        return
    ext = image_path.suffix.lower()
    mime = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif"}.get(ext, "jpeg")
    b64 = _encode_file_b64(image_path)
    att = "fixed" if attach_fixed else "scroll"
    darken = max(0.0, min(0.9, float(darken)))
    blur_px = max(0, min(40, int(blur_px)))
    size_mode = size_mode if size_mode in ("cover", "contain") else "cover"
    st.markdown(f"""
    <style>
    .stApp {{ background: transparent !important; }}
    .block-container {{ position: relative; z-index: 1; }}
    .stApp::before {{
      content: ""; position: fixed; inset: 0;
      background-image: url("data:image/{mime};base64,{b64}");
      background-position: center center; background-repeat: no-repeat;
      background-size: {size_mode}; background-attachment: {att}; filter: blur({blur_px}px); z-index: 0;
    }}
    .stApp::after {{
      content: ""; position: fixed; inset: 0; background: rgba(0,0,0,{darken}); z-index: 0; pointer-events: none;
    }}
    </style>
    """, unsafe_allow_html=True)

# ========== GATE OPCIONAL (senha) ==========
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

# ========== SECRETS → ENV ==========
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
        "DB_BACKEND":         sec.get("DB_BACKEND", "mongo"),
        "APP_ENV":            sec.get("APP_ENV", "dev"),   # dev|prod
        "HF_TOKEN":           sec.get("HF_TOKEN", ""),     # ← para comics/FAL
    }
    for k, v in mapping.items():
        if v and not os.environ.get(k):
            os.environ[k] = str(v)

_load_env_from_secrets()
APP_ENV = os.environ.get("APP_ENV", "dev").lower().strip()

# ========== REGISTRY / ROUTER FALLBACKS ==========
def _safe_error(msg: str, exc: Exception | None = None):
    """Erro verboso em dev, curto em prod (stack guardado no session_state)."""
    if APP_ENV == "prod":
        st.error(msg)
        if exc:
            st.session_state["last_traceback"] = traceback.format_exc()
    else:
        if exc:
            st.error(msg + f"\n\n**{exc.__class__.__name__}:** {exc}\n\n```\n{traceback.format_exc()}\n```")
        else:
            st.error(msg)

try:
    from characters.registry import get_service, list_characters
except Exception as e:
    _safe_error("Falha ao importar `characters.registry`.", e)
    st.stop()

try:
    from core.service_router import available_providers, list_models, chat as provider_chat, route_chat_strict
except Exception:
    def available_providers():
        provs = []
        provs.append(("OpenRouter", bool(os.environ.get("OPENROUTER_API_KEY")), "OK" if os.environ.get("OPENROUTER_API_KEY") else "sem chave"))
        provs.append(("Together",   bool(os.environ.get("TOGETHER_API_KEY")), "OK" if os.environ.get("TOGETHER_API_KEY") else "sem chave"))
        return provs
    def list_models(_p: str | None = None):
        return []
    def provider_chat(model: str, messages: List[dict], **kw):
        raise RuntimeError("service_router indisponível.")
    def route_chat_strict(model: str, payload: dict):
        raise RuntimeError("service_router indisponível.")

# ========== DB HELPERS (fallbacks) ==========
try:
    from core.database import get_backend, set_backend, ping_db, get_col, db_status
except Exception:
    def get_backend() -> str: return os.environ.get("DB_BACKEND", "memory")
    def set_backend(kind: str) -> None: os.environ["DB_BACKEND"] = kind
    def ping_db(): return ("memory", True, "memória local")
    def get_col(_name: str): raise RuntimeError("DB indisponível")
    def db_status(): return ("unknown", "core.database ausente")

# Força Mongo como padrão se nada tiver sido escolhido explicitamente
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
        set_fact, get_fact, get_facts, delete_fact,
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
    def delete_all_user_data(_u: str): return {"hist": 0, "state": 0, "eventos": 0}
    def register_event(*a, **k): ...
    def list_events(_u: str, limit: int = 5): return []
    def save_interaction(*a, **k): ...
    def delete_fact(*a, **k): ...

# ========== SIDEBAR: Provedores + DB ==========
st.sidebar.subheader("🧠 Provedores LLM")
prov_status = {name: ok for (name, ok, _) in available_providers()}
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
            _safe_error("Falha no diagnóstico Mongo.", e)

st.sidebar.markdown("---")

# ========== ESTADO BASE ==========
st.session_state.setdefault("user_id", "Janio Donisete")
st.session_state.setdefault("character", "Mary")

# ----- LISTA DE MODELOS -----
FORCED_MODELS = [
    "deepseek/deepseek-chat-v3-0324",
    "anthropic/claude-3.5-haiku",
    "qwen/qwen3-max",
    "nousresearch/hermes-3-llama-3.1-405b",
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
    "together/Qwen/Qwen2.5-72B-Instruct",
    "together/Qwen/QwQ-32B",
    "inclusionai/ling-1t",
    "z-ai/glm-4.6",
    "thedrummer/cydonia-24b-v4.1",
    "x-ai/grok-4-fast",
    "moonshotai/Kimi-K2-Instruct-0905",
    "deepseek-ai/DeepSeek-R1-0528-tput",
    "x-ai/grok-code-fast-1",
    "google/gemma-3-27b-it",
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
]

def _merge_models() -> List[str]:
    try:
        models_router = list_models(None) or []
    except Exception:
        models_router = []
    models = list(dict.fromkeys(models_router + FORCED_MODELS))
    return models

def _provider_for(model_id: str) -> str:
    m = (model_id or "").lower()
    if any(m.startswith(x) for x in ("together/", "moonshotai/kimi-k2-instruct-0905",
                                     "google/gemma-3-27b-it", "google/gemini-2.5-flash")):
        return "Together"
    if any(m.startswith(x) for x in ("anthropic/","qwen/","nousresearch/","deepseek/","inclusionai/",
                                     "z-ai/","thedrummer/","x-ai/","openai/")):
        return "OpenRouter"
    return "OpenRouter"

def _has_creds_for(model_id: str) -> bool:
    prov = _provider_for(model_id)
    if prov == "Together":
        return bool(os.environ.get("TOGETHER_API_KEY"))
    return bool(os.environ.get("OPENROUTER_API_KEY"))

def _light_ping_model(model_id: str) -> bool:
    if "service_router" in sys.modules:
        try:
            route_chat_strict(model_id, {
                "model": model_id,
                "messages": [{"role": "system", "content": "ping"},
                             {"role": "user", "content": "pong?"}],
                "max_tokens": 4, "temperature": 0.0
            })
            return True
        except Exception:
            return False
    return True

try:
    all_models = _merge_models()
except Exception:
    all_models = FORCED_MODELS[:]

st.session_state.setdefault("model", (all_models[0] if all_models else "deepseek/deepseek-chat-v3-0324"))
st.session_state.setdefault("history", [])  # List[Tuple[str, str]]
st.session_state.setdefault("history_loaded_for", "")
st.session_state.setdefault("_active_key", "")

# ========== CONTROLES TOPO ==========
c1, c2 = st.columns([2, 2])
with c1:
    st.text_input("👤 Usuário", key="user_id", placeholder="Seu nome ou identificador")
with c2:
    names = list_characters()
    default_idx = names.index("Mary") if "Mary" in names else 0
    st.selectbox("🎭 Personagem", names, index=default_idx, key="character")

def _label_model(mid: str) -> str:
    prov = _provider_for(mid)
    tag = "" if mid in (list_models(None) or []) else " • forçado"
    return f"{prov} • {mid}{tag}"

_prev_model = st.session_state.get("_last_model_id", st.session_state.get("model"))
sel = st.selectbox(
    "🧠 Modelo",
    all_models,
    index=all_models.index(st.session_state["model"]) if st.session_state["model"] in all_models else 0,
    format_func=_label_model,
    key="model"
)
if sel != _prev_model:
    if not _has_creds_for(sel):
        st.warning("Este modelo requer credenciais do provedor correspondentes. Revertendo para o anterior.")
        st.session_state["model"] = _prev_model
    else:
        ok = _light_ping_model(sel)
        if not ok:
            st.warning("Modelo não disponível no roteador atual. Revertendo.")
            st.session_state["model"] = _prev_model
        else:
            st.session_state["_last_model_id"] = sel

@st.cache_resource
def _mongo():
    """Retorna coleção MongoDB com cache de conexão."""
    try:
        mongo_user = st.secrets.get("MONGO_USER", "")
        mongo_pass = st.secrets.get("MONGO_PASS", "")
        mongo_cluster = st.secrets.get("MONGO_CLUSTER", "")
        if not (mongo_user and mongo_pass and mongo_cluster):
            return None
        uri = f"mongodb+srv://{mongo_user}:{mongo_pass}@{mongo_cluster}/?retryWrites=true&w=majority"
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        db = client["roleplay_mary"]
        coll = db["interacoes"]
        coll.find_one()  # ping
        return coll
    except Exception as e:
        st.error(f"❌ Erro ao conectar MongoDB: {e}")
        return None

def _save_json_response_to_mongo(data: dict, *, user: str, personagem: str, modelo: str) -> None:
    """Salva resposta JSON estruturada no MongoDB com cache de conexão."""
    try:
        coll = _mongo()
        if not coll:
            st.warning("⚠️ Credenciais do Mongo ausentes em st.secrets.")
            return
        doc = {
            "usuario": user,
            "personagem": personagem,
            "fala": (data.get("fala") or "").strip(),
            "pensamento": (data.get("pensamento") or "").strip(),
            "acao": (data.get("acao") or "").strip(),
            "meta": (data.get("meta") or "").strip(),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "modelo": modelo,
            "modo_json": True,
        }
        coll.insert_one(doc)
    except Exception as e:
        st.error(f"❌ Erro ao salvar no MongoDB: {e}")

def render_assistant_bubbles(markdown_text: str) -> None:
    """
    Renderiza respostas da assistente. Se vier JSON válido (schema: fala/pensamento/acao/meta),
    formata; caso contrário, renderiza Markdown normal.
    """
    if not markdown_text:
        return

    # 1) Tenta JSON estruturado
    try:
        import json as _json
        data = _json.loads(markdown_text)
        if isinstance(data, dict) and ("fala" in data or "pensamento" in data or "acao" in data or "meta" in data):
            fala = str(data.get("fala", "") or "").strip()
            pensamento = str(data.get("pensamento", "") or "").strip()
            acao = str(data.get("acao", "") or "").strip()
            meta = str(data.get("meta", "") or "").strip()

            if fala:
                safe_fala = html.escape(fala).replace("\n", "<br>")
                st.markdown(f"<div class='assistant-paragraph'><b>{safe_fala}</b></div>", unsafe_allow_html=True)
            if pensamento:
                safe_pense = html.escape(pensamento).replace("\n", "<br>")
                st.markdown(f"<div class='assistant-paragraph'><em>{safe_pense}</em></div>", unsafe_allow_html=True)
            if acao:
                safe_acao = html.escape(acao).replace("\n", "<br>")
                st.caption(safe_acao)
            if meta:
                safe_meta = html.escape(meta).replace("\n", "<br>")
                st.caption(safe_meta)

            # Log Mongo (personagem atual)
            try:
                _user = st.session_state.get("user_name") or st.session_state.get("usuario") or "desconhecido"
                _person = (st.session_state.get("character") or "desconhecida").strip()
                _model = st.session_state.get("model") or st.session_state.get("current_model") or "desconhecido"
                _save_json_response_to_mongo(data, user=_user, personagem=_person, modelo=_model)
            except Exception:
                pass

            return
    except Exception:
        pass

    # 2) Fallback: Markdown por parágrafo e blocos de código
    parts = re.split(r"(```[\\s\\S]*?```)", markdown_text)
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            st.markdown(part)
        else:
            paras = [p.strip() for p in re.split(r"\n\s*\n", part) if p.strip()]
            for p in paras:
                safe = html.escape(p).replace("\n", "<br>")
                st.markdown(f"<div class='assistant-paragraph'>{safe}</div>", unsafe_allow_html=True)

def _user_keys_for_history(user_id: str, character_name: str) -> List[str]:
    ch = (character_name or "").strip().lower()
    primary = f"{user_id}::{ch}"
    if ch == "mary":
        return [primary, user_id]
    return [primary]

def _reload_history(force: bool = False):
    user_id = str(st.session_state["user_id"])
    char = str(st.session_state["character"])
    key = f"{user_id}|{char}|{get_backend()}"
    if not force and st.session_state["history_loaded_for"] == key:
        return
    try:
        keys = _user_keys_for_history(user_id, char)
        docs = get_history_docs_multi(keys, limit=400) or []
        hist: List[Tuple[str, str]] = []

        resposta_key = f"resposta_{char.strip().lower()}"
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get(resposta_key)
                 or d.get("resposta_adelle")  # compatibilidade Adelle
                 or d.get("resposta_mary") or "").strip()
            if u:
                hist.append(("user", u))
            if a:
                hist.append(("assistant", a))
        st.session_state["history"] = hist
        st.session_state["history_loaded_for"] = key
    except Exception as e:
        _safe_error("Não foi possível carregar o histórico.", e)

# ========== Boot da First Message ==========
try:
    user_id = str(st.session_state.get("user_id", "")).strip()
    char    = str(st.session_state.get("character", "")).strip()
    if user_id and char:
        char_key = f"{user_id}::{char.lower()}"
        docs_exist = False
        try:
            existing = get_history_docs(char_key) or []
            docs_exist = len(existing) > 0
        except Exception:
            existing = []
            docs_exist = False

        if not docs_exist:
            try:
                mod = __import__(f"characters.{char.lower()}.persona", fromlist=["get_persona"])
                get_persona = getattr(mod, "get_persona", None)
            except Exception:
                get_persona = None

            if callable(get_persona):
                persona_text, history_boot = get_persona()
                first_msg = next((m.get("content","") for m in (history_boot or [])
                                  if (m.get("role") or "") == "assistant"), "").strip()
                if first_msg:
                    try:
                        save_interaction(char_key, "", first_msg, "boot:first_message")
                    except Exception:
                        pass
except Exception as e:
    _safe_error("Boot da primeira mensagem falhou.", e)

# ========== Troca de thread ==========
_current_active = f"{st.session_state['user_id']}::{str(st.session_state['character']).lower()}"
if st.session_state["_active_key"] != _current_active:
    from characters.registry import clear_service_cache
    clear_service_cache(st.session_state.get("character", ""))  # limpa só a atual
    st.session_state["_active_key"] = _current_active
    st.session_state["history"] = []
    st.session_state["history_loaded_for"] = ""
    _reload_history(force=True)
# ========== Auto-seed: Mary ==========
try:
    _user = str(st.session_state.get("user_id", "")).strip()
    _char = str(st.session_state.get("character", "")).strip().lower()
    if _user and _char == "mary":
        _mary_key = f"{_user}::mary"
        try:
            f = get_facts(_mary_key) or {}
        except Exception:
            f = {}
        changed = False
        if not str(f.get("parceiro_atual", "")).strip():
            set_fact(_mary_key, "parceiro_atual", _user, {"fonte": "auto_seed"}); changed = True
        if "casados" not in f:
            set_fact(_mary_key, "casados", True, {"fonte": "auto_seed"}); changed = True
        if not str(f.get("local_cena_atual", "")).strip():
            set_fact(_mary_key, "local_cena_atual", "quarto", {"fonte": "auto_seed"}); changed = True
        if changed:
            st.session_state["history_loaded_for"] = ""
            _reload_history(force=True)
except Exception as _e:
    _safe_error("Auto-seed Mary falhou.", _e)

# ========== Auto-seed: Adelle ==========
try:
    _user = str(st.session_state.get("user_id", "")).strip()
    _char = str(st.session_state.get("character", "")).strip().lower()
    if _user and _char == "adelle":
        _ad_key = f"{_user}::adelle"
        try:
            f = get_facts(_ad_key) or {}
        except Exception:
            f = {}
        changed = False
        if not str(f.get("nome_agente", "")).strip():
            set_fact(_ad_key, "nome_agente", _user, {"fonte": "auto_seed"}); changed = True
        if not str(f.get("adelle.missao.objetivo", "")).strip():
            set_fact(_ad_key, "adelle.missao.objetivo", "Destruir a família Roytmann", {"fonte": "auto_seed"}); changed = True
        if not str(f.get("local_cena_atual", "")).strip():
            set_fact(_ad_key, "local_cena_atual", "sala de debriefing", {"fonte": "auto_seed"}); changed = True
        if changed:
            st.session_state["history_loaded_for"] = ""
            _reload_history(force=True)
except Exception as _e:
    _safe_error("Auto-seed Adelle falhou.", _e)

from characters.registry import clear_service_cache
clear_service_cache(st.session_state.get("character", ""))

# ========== Instancia serviço ==========
try:
    service = get_service(st.session_state["character"])
except Exception as e:
    _safe_error("Falha ao instanciar serviço da personagem.", e)
    st.stop()

st.sidebar.caption(f"⚙️ Service ativo: {service.__class__.__name__} @ {getattr(service, '__module__', '?')}")

_char = str(st.session_state.get("character","")).strip().lower()
mod_name = f"characters.{_char}.service"
cls_name = f"{_char.capitalize()}Service"
try:
    mod = importlib.import_module(mod_name)
    ok_cls = hasattr(mod, cls_name)
    st.sidebar.write(f"🔎 import {mod_name}: OK")
    st.sidebar.write(f"🔎 classe {cls_name}: {'OK' if ok_cls else 'NÃO ENCONTRADA'}")
    if not ok_cls:
        st.sidebar.code(dir(mod))
except Exception as e:
    st.sidebar.error(f"Falhou ao importar {mod_name}: {e}")
    st.sidebar.code(traceback.format_exc())


# ========== Sidebar específico da personagem ==========
with st.sidebar:
    st.caption("◻️ Sidebar base do app ativo")
    render_sidebar = getattr(service, "render_sidebar", None)
    if callable(render_sidebar):
        try:
            st.caption("◻️ Hook de sidebar da personagem carregado")
            # Passe 'st.sidebar' se seu service espera explicitamente um container de sidebar
            render_sidebar(st.sidebar)
        except Exception as e:
            _safe_error(f"Erro no sidebar de {getattr(service, 'display_name', 'personagem')}.", e)
    else:
        st.caption("Sem preferências para esta personagem.")

# ========== Sidebar: Manutenção ==========
st.sidebar.markdown("---")
st.sidebar.subheader("🧹 Manutenção")

def _force_reload_history_ui():
    st.session_state["history"] = []
    st.session_state["history_loaded_for"] = ""
    _reload_history(force=True)

colA, colB = st.sidebar.columns(2)

if colA.button("⏪ Apagar último turno"):
    try:
        _user_id = str(st.session_state.get("user_id", ""))
        _char    = str(st.session_state.get("character", "")).strip().lower()
        _key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id
        _key_legacy  = _user_id if _char == "mary" else None

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
        _safe_error("Falha ao apagar último turno.", e)

if colB.button("🔄 Resetar histórico"):
    try:
        _user_id = str(st.session_state.get("user_id", ""))
        _char    = str(st.session_state.get("character", "")).strip().lower()
        _key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id
        _key_legacy  = _user_id if _char == "mary" else None

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
        _safe_error("Falha ao resetar histórico.", e)

if st.sidebar.button("🧨 Apagar TUDO (chat + memórias)"):
    try:
        _user_id = str(st.session_state.get("user_id", ""))
        _char    = str(st.session_state.get("character", "")).strip().lower()
        _key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id
        _key_legacy  = _user_id if _char == "mary" else None

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
        _safe_error("Falha ao apagar TUDO.", e)

# ===== BOTÃO: FORÇAR RELOAD PERSONA =====
if st.sidebar.button("🔄 Forçar Reload Persona"):
    try:
        import importlib
        _user_id = str(st.session_state.get("user_id", ""))
        _char    = str(st.session_state.get("character", "")).strip().lower()
        _key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id
        _key_legacy  = _user_id if _char == "nerith" else None

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

        mod_name = f"characters.{_char}.persona"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
            st.sidebar.info(f"✅ Módulo {mod_name} removido do cache Python")

        st.cache_data.clear()
        st.cache_resource.clear()

        st.session_state.history = []
        st.session_state.history_loaded_for = ""

        # limpar __pycache__ (best-effort)
        try:
            import subprocess
            subprocess.run(['find', '.', '-type', 'd', '-name', '__pycache__', '-exec', 'rm', '-rf', '{}', '+'],
                           capture_output=True, timeout=5)
        except Exception:
            pass

        st.sidebar.success(f"✅ Persona recarregada! ({total} docs deletados)")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"❌ Erro: {e}")

# Botão limpar cache
if st.sidebar.button("🧹 Limpar cache (dados/recursos)"):
    try: st.cache_data.clear()
    except Exception: pass
    try: st.cache_resource.clear()
    except Exception: pass
    st.sidebar.success("Caches limpos. (Se algo estranho persistir, atualize a página.)")

# Botão atualizar mensagem inicial
if st.sidebar.button("🔄 Atualizar Mensagem Inicial"):
    try:
        _user_id = str(st.session_state.get("user_id", ""))
        _char    = str(st.session_state.get("character", "")).strip().lower()
        _key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id
        _key_legacy  = _user_id if _char == "mary" else None

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

        try: st.cache_data.clear()
        except Exception: pass
        try: st.cache_resource.clear()
        except Exception: pass

        st.session_state["history"] = []
        st.session_state["history_loaded_for"] = ""

        st.sidebar.success(f"✅ Mensagem inicial atualizada! ({total} itens deletados)")
        st.rerun()
    except Exception as e:
        _safe_error("Falha ao atualizar mensagem inicial.", e)

# ========== Sidebar: Memória Canônica ==========
st.sidebar.subheader("🧠 Memória Canônica")

_user_id = str(st.session_state.get("user_id", ""))
_char    = str(st.session_state.get("character", "")).strip().lower()
user_key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id

facts = {}
try:
    facts = get_facts(user_key_primary) or {}
except Exception as e:
    _safe_error("Não foi possível ler memórias.", e)

if facts:
    for k, v in facts.items():
        st.sidebar.write(f"- `{k}` → {v}")
else:
    st.sidebar.caption("_Sem memórias salvas para esta personagem._")

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
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                _safe_error("Falha ao salvar memória.", e)

if facts:
    with st.sidebar.form("form_del_fact", clear_on_submit=True):
        st.markdown("**Remover memória**")
        del_key = st.selectbox("Chave", sorted(facts.keys()))
        ok2 = st.form_submit_button("🗑️ Remover")
        if ok2 and del_key:
            try:
                delete_fact(user_key_primary, del_key)
                st.success("Memória removida.")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                _safe_error("Falha ao remover memória.", e)

with st.sidebar.expander("🗂️ Memória por personagem"):
    try:
        for name in list_characters():
            k = f"{_user_id}::{name.lower()}"
            try:
                f = get_facts(k) or {}
                st.write(f"**{name}** — {len(f)} memórias")
                for kk, vv in list(f.items())[:8]:
                    st.caption(f"`{kk}` → {vv}")
            except Exception:
                st.caption(f"**{name}** — erro ao ler")
    except Exception:
        st.caption("Não foi possível listar personagens.")

# --- Seed rápido: Laura + Janio ---
with st.sidebar.expander("⚡ Seed rápido: Laura + Janio", expanded=False):
    u = (st.session_state.get("user_id") or "Janio Donisete").strip()
    target = f"{u}::laura"
    st.caption("Grava memórias canônicas da Laura para o usuário atual e registra o primeiro encontro no Posto 6.")
    if st.button("Aplicar seed (Laura ❤️ Janio)"):
        try:
            set_fact(target, "parceiro_atual", u, {"fonte": "seed"})
            set_fact(target, "status_relacao", "paixao_secreta", {"fonte": "seed"})
            set_fact(target, "sonho", "casar_e_formar_familia", {"fonte": "seed"})
            set_fact(target, "nao_faz_programa", True, {"fonte": "seed"})
            set_fact(target, "local_cena_atual", "Quiosque Posto 6", {"fonte": "seed"})
            register_event(target, "primeiro_encontro", "Drinks e petiscos no Posto 6.", "Posto 6", {"iniciado_por": u})
            st.success("Seed aplicado para Laura. Abra o chat com a Laura para ver o efeito.")
            st.session_state["history_loaded_for"] = ""
            st.rerun()
        except Exception as e:
            _safe_error("Falha ao aplicar seed Laura.", e)

with st.sidebar.expander("🔓 NSFW rápido: Laura", expanded=False):
    u2 = (st.session_state.get("user_id") or "Janio Donisete").strip()
    target2 = f"{u2}::laura"
    if st.button("Ativar NSFW para Laura"):
        try:
            set_fact(target2, "nsfw_override", "on", {"fonte": "seed"})
            st.success("NSFW ON para Laura.")
        except Exception as e:
            _safe_error("Falha ao ativar NSFW para Laura.", e)

# --- Seed rápido: Mary (Esposa Cúmplice) ---
with st.sidebar.expander("⚡ Seed rápido: Mary (Esposa Cúmplice)", expanded=False):
    u = (st.session_state.get("user_id") or "").strip() or "Janio Donisete"
    mary_key = f"{u}::mary"
    st.caption("Grava memórias canônicas da Mary casada com o usuário atual e define local inicial para 'quarto'.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Aplicar seed Mary"):
            try:
                set_fact(mary_key, "parceiro_atual", u, {"fonte": "seed"})
                set_fact(mary_key, "casados", True, {"fonte": "seed"})
                set_fact(mary_key, "local_cena_atual", "quarto", {"fonte": "seed"})
                st.success("Seed aplicado para Mary (Esposa Cúmplice).")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                _safe_error("Falha ao aplicar seed Mary.", e)
    with col2:
        if st.button("Limpar 'casados'"):
            try:
                set_fact(mary_key, "casados", False, {"fonte": "seed"})
                st.success("Flag 'casados' definido como False.")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                _safe_error("Falha ao limpar 'casados'.", e)

# --- Seed rápido: Adelle (Diplomata Exilada) ---
with st.sidebar.expander("⚡ Seed rápido: Adelle (Diplomata Exilada)", expanded=False):
    u = (st.session_state.get("user_id") or "Janio Donisete").strip()
    adelle_key = f"{u}::adelle"
    st.caption("Grava briefing inicial da missão e define local inicial da cena.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Aplicar seed Adelle"):
            try:
                set_fact(adelle_key, "nome_agente", u, {"fonte": "seed"})
                set_fact(adelle_key, "adelle.missao.objetivo", "Destruir a família Roytmann", {"fonte": "seed"})
                set_fact(adelle_key, "adelle.missao.alvos", "Florêncio, Heitor, Pietro, Neuza", {"fonte": "seed"})
                set_fact(adelle_key, "adelle.missao.ponto_fraco", "Sophia Roytmann (filha ingênua)", {"fonte": "seed"})
                set_fact(adelle_key, "adelle.entity.alvo_principal", "Pietro Roytmann", {"fonte": "seed"})
                set_fact(adelle_key, "adelle.entity.local_seguro", "flat de cobertura no Centro", {"fonte": "seed"})
                if not str(get_fact(adelle_key, "local_cena_atual", "")).strip():
                    set_fact(adelle_key, "local_cena_atual", "sala de debriefing", {"fonte": "seed"})
                st.success("Seed aplicado para Adelle (briefing + entidades + local).")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                _safe_error("Falha ao aplicar seed Adelle.", e)
    with col2:
        if st.button("Limpar briefing Adelle"):
            try:
                for k in [
                    "nome_agente", "adelle.missao.objetivo", "adelle.missao.alvos",
                    "adelle.missao.ponto_fraco", "adelle.entity.alvo_principal", "adelle.entity.local_seguro"
                ]:
                    try: delete_fact(adelle_key, k)
                    except Exception: pass
                st.success("Briefing/entidades limpos (Adelle).")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                _safe_error("Falha ao limpar briefing da Adelle.", e)

# ========== Sidebar: NSFW & Primeira vez ==========
st.sidebar.markdown("---")
st.sidebar.subheader("🔞 NSFW & Primeira vez")

try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_k: str) -> bool:
        return str(get_fact(_k, "nsfw_override", "")).lower() == "on"

_user_id = str(st.session_state.get("user_id", "")).strip()
_char    = str(st.session_state.get("character", "")).strip().lower()
user_key = f"{_user_id}::{_char}" if _user_id and _char else _user_id

try:
    NSFW_ON = bool(nsfw_enabled(user_key))
except Exception:
    NSFW_ON = False

virgem_val = get_fact(user_key, "virgem", None)
virg_caption = "—" if virgem_val is None else ("Sim" if virgem_val else "Não")

st.sidebar.caption(f"Status NSFW: **{'✅ ON' if NSFW_ON else '🔒 OFF'}**")
st.sidebar.caption(f"Virgindade: **{virg_caption}**")

c_on, c_off = st.sidebar.columns(2)
if c_on.button("🔓 Liberar NSFW"):
    try:
        set_fact(user_key, "virgem", False, {"fonte": "sidebar"})
        set_fact(user_key, "nsfw_override", "on", {"fonte": "sidebar"})
        local_atual = get_fact(user_key, "local_cena_atual", None)
        register_event(user_key, "primeira_vez", f"{st.session_state.get('character','?')} teve sua primeira vez.", local_atual, {"origin": "sidebar"})
        st.sidebar.success("NSFW liberado e 'primeira_vez' registrado.")
        st.session_state["history_loaded_for"] = ""
        st.rerun()
    except Exception as e:
        _safe_error("Falha ao liberar NSFW.", e)

if c_off.button("🔒 Bloquear NSFW"):
    try:
        set_fact(user_key, "nsfw_override", "off", {"fonte": "sidebar"})
        st.sidebar.success("NSFW bloqueado para esta personagem/usuário.")
        st.session_state["history_loaded_for"] = ""
        st.rerun()
    except Exception as e:
        _safe_error("Falha ao bloquear NSFW.", e)

# ========== Sidebar: Plano de fundo ==========
st.sidebar.markdown("---")
st.sidebar.subheader("🖼️ Plano de fundo")

bg_files = []
for pattern in ("nerith*.jpg","nerith*.jpeg","nerith*.png","nerith*.webp",
                "mary*.jpg","mary*.jpeg","mary*.png","mary*.webp",
                "adelle*.jpg","adelle*.jpeg","adelle*.png","adelle*.webp"):
    bg_files += list(IMG_DIR.glob(pattern))
bg_files = sorted({p.name: p for p in bg_files}.values(), key=lambda p: p.name)

choices = ["(nenhuma)"] + [p.name for p in bg_files]
st.session_state.setdefault("bg_file", choices[1] if len(choices) > 1 else "(nenhuma)")
st.session_state.setdefault("bg_darken", 25)
st.session_state.setdefault("bg_blur", 0)
st.session_state.setdefault("bg_fixed", True)
st.session_state.setdefault("bg_size", "cover")

bg_sel = st.sidebar.selectbox("Imagem", choices, index=choices.index(st.session_state["bg_file"]))
bg_path = (IMG_DIR / bg_sel) if bg_sel != "(nenhuma)" else None

if bg_path and bg_path.exists():
    try:
        size_mb = bg_path.stat().st_size / (1024*1024)
        st.sidebar.image(str(bg_path), caption=f"Preview ({size_mb:.2f} MB)", use_container_width=True)
        if size_mb > 3.5:
            st.sidebar.warning("Imagem grande pode deixar o app pesado. Considere comprimir/redimensionar.")
    except Exception:
        pass

bg_darken = st.sidebar.slider("Escurecer overlay (%)", 0, 90, st.session_state["bg_darken"])
bg_blur = st.sidebar.slider("Desfoque (px)", 0, 20, st.session_state["bg_blur"])
bg_fixed = st.sidebar.checkbox("Fundo fixo", value=st.session_state["bg_fixed"])
bg_size  = st.sidebar.selectbox("Ajuste", ["cover", "contain"], index=(0 if st.session_state["bg_size"]=="cover" else 1))

st.session_state["bg_file"] = bg_sel
st.session_state["bg_darken"] = bg_darken
st.session_state["bg_blur"] = bg_blur
st.session_state["bg_fixed"] = bg_fixed
st.session_state["bg_size"] = bg_size

if bg_path and bg_path.exists():
    set_background(bg_path, darken=bg_darken/100.0, blur_px=bg_blur, attach_fixed=bg_fixed, size_mode=bg_size)

# ========== Preferências rápidas (Mary/Adelle) ==========
st.sidebar.markdown("---")
st.sidebar.subheader("🎚️ Preferências (Mary)")
if _char == "mary":
    nivel_opts = ["sutil","media","alta"]
    ritmo_opts = ["lento","normal","rapido"]
    tam_opts   = ["curta","media","longa"]

    def _idx_safe(opts: List[str], val: str, default_idx: int) -> int:
        try:
            return opts.index(val.lower())
        except Exception:
            return default_idx

    nivel_cur = str(facts.get("mary.pref.nivel_sensual","sutil")).lower() if facts else "sutil"
    ritmo_cur = str(facts.get("mary.pref.ritmo","lento")).lower() if facts else "lento"
    tam_cur   = str(facts.get("mary.pref.tamanho_resposta","media")).lower() if facts else "media"

    nivel = st.sidebar.selectbox("Nível sensual", nivel_opts, index=_idx_safe(nivel_opts, nivel_cur, 0))
    ritmo = st.sidebar.selectbox("Ritmo", ritmo_opts, index=_idx_safe(ritmo_opts, ritmo_cur, 0))
    tam   = st.sidebar.selectbox("Tamanho da resposta", tam_opts, index=_idx_safe(tam_opts, tam_cur, 1))
    if st.sidebar.button("💾 Salvar preferências"):
        try:
            set_fact(user_key, "mary.pref.nivel_sensual", nivel, {"fonte":"prefs"})
            set_fact(user_key, "mary.pref.ritmo", ritmo, {"fonte":"prefs"})
            set_fact(user_key, "mary.pref.tamanho_resposta", tam, {"fonte":"prefs"})
            st.sidebar.success("Preferências salvas.")
            st.rerun()
        except Exception as e:
            _safe_error("Falha ao salvar preferências.", e)

elif _char == "adelle":
    st.sidebar.subheader("🎚️ Preferências (Adelle)")
    abordagem_opts = ["calculista","agressiva","sedutora"]
    ritmo_opts     = ["lento","moderado","rapido"]
    tam_opts       = ["curta","media","longa"]

    def _idx_safe2(opts: List[str], val: str, default_idx: int) -> int:
        try:
            return opts.index((val or "").lower())
        except Exception:
            return default_idx

    ab_cur  = str(facts.get("adelle.pref.abordagem","calculista")).lower() if facts else "calculista"
    rt_cur  = str(facts.get("adelle.pref.ritmo_trama","moderado")).lower() if facts else "moderado"
    tam_cur = str(facts.get("adelle.pref.tamanho_resposta","media")).lower() if facts else "media"

    abordagem = st.sidebar.selectbox("Abordagem", abordagem_opts, index=_idx_safe2(abordagem_opts, ab_cur, 0))
    ritmo_t   = st.sidebar.selectbox("Ritmo da trama", ritmo_opts, index=_idx_safe2(ritmo_opts, rt_cur, 1))
    tam       = st.sidebar.selectbox("Tamanho da resposta", tam_opts, index=_idx_safe2(tam_opts, tam_cur, 1))
    if st.sidebar.button("💾 Salvar preferências (Adelle)"):
        try:
            set_fact(user_key, "adelle.pref.abordagem", abordagem, {"fonte":"prefs"})
            set_fact(user_key, "adelle.pref.ritmo_trama", ritmo_t, {"fonte":"prefs"})
            set_fact(user_key, "adelle.pref.tamanho_resposta", tam, {"fonte":"prefs"})
            st.sidebar.success("Preferências salvas (Adelle).")
            st.rerun()
        except Exception as e:
            _safe_error("Falha ao salvar preferências (Adelle).", e)

# ========== Janela de contexto ==========
st.sidebar.markdown("---")
st.sidebar.subheader("🧾 Janela de contexto")
st.session_state.setdefault("verbatim_ultimos", 10)
st.session_state["verbatim_ultimos"] = st.sidebar.slider(
    "Turnos verbatim (pares recentes)", 4, 18, st.session_state["verbatim_ultimos"]
)

# ========== Carrega & Render histórico ==========
_reload_history()

_last_role, _last_content = None, None
for role, content in st.session_state["history"]:
    if role == _last_role and content == _last_content:
        continue
    _last_role, _last_content = role, content
    with st.chat_message("user" if role == "user" else "assistant",
                         avatar=("💬" if role == "user" else "💚")):
        if role == "assistant":
            render_assistant_bubbles(content)
            # disponibiliza para o provider de cena (quadrinhos)
            st.session_state["last_assistant_message"] = content
        else:
            st.markdown(content)

# ========== LLM Ping ==========
with st.expander("🔧 Diagnóstico LLM"):
    can_ping = _has_creds_for(st.session_state["model"])
    if not can_ping:
        st.caption("Sem credenciais para o provedor do modelo atual — ping desabilitado.")
    if st.button("Ping modelo atual", disabled=not can_ping):
        try:
            try:
                from core.service_router import route_chat_strict as _route
                data = _route(
                    st.session_state["model"],
                    {
                        "model": st.session_state["model"],
                        "messages": [
                            {"role": "system", "content": "Você é um ping de diagnóstico. Responda com 'pong'."},
                            {"role": "user", "content": "diga: pong"},
                        ],
                        "max_tokens": 16, "temperature": 0.0, "top_p": 1.0,
                    }
                )[0]
                txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
                st.success(f"{st.session_state['model']} → {txt!r}")
            except Exception:
                data, used, prov = provider_chat(
                    st.session_state["model"],
                    messages=[
                        {"role": "system", "content": "Você é um ping de diagnóstico. Responda com 'pong'."},
                        {"role": "user", "content": "diga: pong"},
                    ],
                    max_tokens=16, temperature=0.0, top_p=1.0,
                )
                txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
                st.success(f"{prov} • {used} → {txt!r}")
        except Exception as e:
            _safe_error("LLM ping falhou.", e)

# ========== Helper de chamada segura ==========
def _safe_reply_call(_service, *, user: str, model: str, prompt: str) -> str:
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
        return fn(user, model, prompt)
    except TypeError:
        return fn(user, model)

# =======================
#  CHAT ROBUSTO (FILA)
# =======================
st.session_state.setdefault("_pending_prompt", None)
st.session_state.setdefault("_pending_auto", False)
st.session_state.setdefault("_is_generating", False)
st.session_state.setdefault("_job_uid", None)
st.session_state.setdefault("_cont_clicked", False)
st.session_state.setdefault("_recap_clicked", False)

# Placeholder dinâmico
_ph = st.session_state.get("suggestion_placeholder", "")
_default_ph = f"Fale com {st.session_state['character']}"
_dyn_ph = f"💡 Sugestão: {_ph}" if _ph else _default_ph

# Chat input
try:
    user_prompt = st.chat_input(_default_ph, placeholder=_dyn_ph, key="chat_msg")
except TypeError:
    user_prompt = st.chat_input(_dyn_ph, key="chat_msg")

# 1) Captura do envio do usuário
if user_prompt and not st.session_state.get("_is_generating"):
    st.session_state["_pending_prompt"] = user_prompt
    st.session_state["_pending_auto"] = False

# 2) Botão CONTINUAR cria job
if st.session_state.get("_cont_clicked") and not st.session_state.get("_is_generating"):
    st.session_state["_pending_prompt"] = (
        "CONTINUAR: Prossiga a cena exatamente de onde a última resposta parou. "
        "Mantenha LOCAL_ATUAL, personagens presentes e tom. Não resuma; avance ação e diálogo em 1ª pessoa."
    )
    st.session_state["_pending_auto"] = True
    st.session_state["_cont_clicked"] = False

# 3) Botão RECAP curto
if st.session_state.get("_recap_clicked") and not st.session_state.get("_is_generating"):
    st.session_state["_pending_prompt"] = (
        "Faça um recap curto telegráfico da conversa recente: nomes próprios, locais/tempo atual, "
        "decisões tomadas e rumo do enredo. Sem diálogos literais."
    )
    st.session_state["_pending_auto"] = False
    st.session_state["_recap_clicked"] = False

# 4) Processa job
_has_job = bool(st.session_state.get("_pending_prompt"))
if _has_job and not st.session_state.get("_is_generating"):
    st.session_state["_is_generating"] = True
    st.session_state["_job_uid"] = f"job-{time.time():.6f}"

    final_prompt = str(st.session_state["_pending_prompt"])
    auto_continue = bool(st.session_state["_pending_auto"])

    # Render turno do usuário
    with st.chat_message("user"):
        st.markdown("🔁 **Continuar**" if auto_continue else final_prompt)
    st.session_state["history"].append(("user", "🔁 Continuar" if auto_continue else final_prompt))

    # Geração protegida
    try:
        with st.spinner("Gerando…"):
            try:
                text = _safe_reply_call(
                    service,
                    user=str(st.session_state["user_id"]),
                    model=str(st.session_state["model"]),
                    prompt=final_prompt,
                )
            except Exception as e:
                tb = traceback.format_exc()
                if APP_ENV == "prod":
                    text = "❌ Ocorreu um erro de geração. Tente novamente em instantes."
                    st.session_state["last_traceback"] = tb
                else:
                    text = f"Erro durante a geração:\n\n**{e.__class__.__name__}** — {e}\n\n```\n{tb}\n```"

        # Append garantido
        if text:
            last = st.session_state["history"][-1] if st.session_state["history"] else None
            if last != ("assistant", text):
                st.session_state["history"].append(("assistant", text))

        # Render da assistente
        with st.chat_message("assistant", avatar="💚"):
            render_assistant_bubbles(text)

    finally:
        # Limpeza SEMPRE
        st.session_state["_pending_prompt"] = None
        st.session_state["_pending_auto"] = False
        st.session_state["_job_uid"] = None
        st.session_state["_is_generating"] = False
