# main.py
from __future__ import annotations

import os
import sys
import time
import hmac
import hashlib
import inspect
import traceback
import base64
import re
import html
from pathlib import Path
from typing import Optional, List, Tuple

import streamlit as st

# ===== Memória longa (garante índices no boot) =====
from core.memoria_longa import ensure_indexes
ensure_indexes()

# ---------- Path / Page ----------
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Config inicial da página
st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="centered")

# ===== CSS global (container central + chat + sem vazamento lateral) =====
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

def _encode_image_b64(p: Path) -> str:
    with p.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def set_background(image_path: Path, *, darken: float = 0.25, blur_px: int = 0,
                   attach_fixed: bool = True, size_mode: str = "cover") -> None:
    if not image_path.exists():
        return
    ext = image_path.suffix.lower()
    mime = {
        ".jpg": "jpeg", ".jpeg": "jpeg",
        ".png": "png", ".webp": "webp", ".gif": "gif"
    }.get(ext, "jpeg")
    with image_path.open("rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
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
    def delete_all_user_data(_u: str): return {"hist": 0, "state": 0, "eventos": 0, "perfil": 0}
    def register_event(*a, **k): ...
    def list_events(_u: str, limit: int = 5): return []
    def save_interaction(*a, **k): ...
    def delete_fact(*a, **k): ...

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
try:
    from core.service_router import list_models as _lm_
    all_models = _lm_(None)
except Exception:
    all_models = [
        "deepseek/deepseek-chat-v3-0324",
        "anthropic/claude-3.5-haiku",
        "qwen/qwen3-max",
        "nousresearch/hermes-3-llama-3.1-405b",
        "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        "together/Qwen/Qwen2.5-72B-Instruct",
        "together/Qwen/QwQ-32B",
    ]

# 👉 Força aparecer o modelo do OpenRouter mesmo se o router não listar
if "inclusionai/ling-1t" not in all_models:
    all_models.append("inclusionai/ling-1t")
# 👉 Força aparecer o modelo do OpenRouter mesmo se o router não listar
if "z-ai/glm-4.6" not in all_models:
    all_models.append("z-ai/glm-4.6")   
# 👉 Força aparecer o modelo do OpenRouter mesmo se o router não listar
if "thedrummer/cydonia-24b-v4.1" not in all_models:
    all_models.append("thedrummer/cydonia-24b-v4.1")  

# 👉 Força aparecer o modelo do OpenRouter mesmo se o router não listar
if "x-ai/grok-4-fast" not in all_models:
    all_models.append("x-ai/grok-4-fast")
# 👉 Força aparecer o modelo do OpenRouter mesmo se o router não listar
if "moonshotai/kimi-k2-0905" not in all_models:
    all_models.append("moonshotai/kimi-k2-0905")
# 👉 Força aparecer o modelo do OpenRouter mesmo se o router não listar
if "x-ai/grok-code-fast-1" not in all_models:
    all_models.append("x-ai/grok-code-fast-1") 

# 👉 Força aparecer o modelo do OpenRouter mesmo se o router não listar
if "google/gemma-3-27b-it" not in all_models:
    all_models.append("google/gemma-3-27b-it")

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

st.selectbox("🧠 Modelo", all_models, key="model")

def render_assistant_bubbles(markdown_text: str) -> None:
    """Renderiza mensagens da assistente em blocos azuis; preserva ```code```."""
    if not markdown_text:
        return
    parts = re.split(r"(```[\s\S]*?```)", markdown_text)
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            st.markdown(part)
        else:
            paras = [p.strip() for p in re.split(r"\n\s*\n", part) if p.strip()]
            for p in paras:
                safe = html.escape(p).replace("\n", "<br>")
                st.markdown(f"<div class='assistant-paragraph'>{safe}</div>", unsafe_allow_html=True)

# ---------- Helpers de histórico ----------
def _user_keys_for_history(user_id: str, character_name: str) -> List[str]:
    ch = (character_name or "").strip().lower()
    primary = f"{user_id}::{ch}"
    if ch == "mary":
        return [primary, user_id]  # inclui legado
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

# --- Boot da First Message (somente se o DB estiver vazio) ---
try:
    user_id = str(st.session_state.get("user_id", "")).strip()
    char    = str(st.session_state.get("character", "")).strip()
    char_key = f"{user_id}::{char.lower()}" if user_id and char else user_id

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
            first_msg = next((m.get("content","") for m in history_boot if m.get("role")=="assistant"), "").strip()
            if first_msg:
                try:
                    save_interaction(char_key, "", first_msg, "boot:first_message")
                except Exception:
                    pass
except Exception as e:
    st.sidebar.warning(f"Boot da First Message falhou: {e}")

# ---------- Troca de thread ao mudar usuário/personagem ----------
_current_active = f"{st.session_state['user_id']}::{str(st.session_state['character']).lower()}"
if st.session_state["_active_key"] != _current_active:
    st.session_state["_active_key"] = _current_active
    st.session_state["history"] = []
    st.session_state["history_loaded_for"] = ""
    _reload_history(force=True)

# --- Auto-seed: Mary (Esposa Cúmplice) ---
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
            set_fact(_mary_key, "parceiro_atual", _user, {"fonte": "auto_seed"})
            changed = True
        if "casados" not in f:
            set_fact(_mary_key, "casados", True, {"fonte": "auto_seed"})
            changed = True
        if not str(f.get("local_cena_atual", "")).strip():
            set_fact(_mary_key, "local_cena_atual", "quarto", {"fonte": "auto_seed"})
            changed = True
        if changed:
            st.session_state["history_loaded_for"] = ""
            _reload_history(force=True)
except Exception as _e:
    st.sidebar.warning(f"Auto-seed Mary falhou: {_e}")

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
_key_legacy  = _user_id if _char == "mary" else None

colA, colB = st.sidebar.columns(2)

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
st.sidebar.subheader("🧠 Memória Canônica")

_user_id = str(st.session_state.get("user_id", ""))
_char    = str(st.session_state.get("character", "")).strip().lower()
user_key_primary = f"{_user_id}::{_char}" if _user_id and _char else _user_id

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
                st.error(f"Falha ao salvar: {e}")

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
                st.error(f"Falha ao remover: {e}")

with st.sidebar.expander("🗂️ Memória por personagem"):
    try:
        from characters.registry import list_characters
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
            st.error(f"Falha ao aplicar seed: {e}")

with st.sidebar.expander("🔓 NSFW rápido: Laura", expanded=False):
    if st.button("Ativar NSFW para Laura"):
        try:
            set_fact(target, "nsfw_override", "on", {"fonte": "seed"})
            st.success("NSFW ON para Laura.")
        except Exception as e:
            st.error(f"Falha ao ativar NSFW: {e}")

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
                st.error(f"Falha ao aplicar seed: {e}")
    with col2:
        if st.button("Limpar 'casados'"):
            try:
                set_fact(mary_key, "casados", False, {"fonte": "seed"})
                st.success("Flag 'casados' definido como False.")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                st.error(f"Falha ao limpar: {e}")

# ---------- Sidebar: NSFW & Primeira vez ----------
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
virg_caption = "—"
if virgem_val is True:
    virg_caption = "Sim"
elif virgem_val is False:
    virg_caption = "Não"

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
        st.sidebar.error(f"Falha ao liberar NSFW: {e}")

if c_off.button("🔒 Bloquear NSFW"):
    try:
        set_fact(user_key, "nsfw_override", "off", {"fonte": "sidebar"})
        st.sidebar.success("NSFW bloqueado para esta personagem/usuário.")
        st.session_state["history_loaded_for"] = ""
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Falha ao bloquear NSFW: {e}")

# ---------- Sidebar: Plano de fundo ----------
st.sidebar.markdown("---")
st.sidebar.subheader("🖼️ Plano de fundo")

bg_files = []
for pattern in ("nerith*.jpg","nerith*.jpeg","nerith*.png","nerith*.webp",
                "mary*.jpg","mary*.jpeg","mary*.png","mary*.webp"):
    bg_files += list(IMG_DIR.glob(pattern))
bg_files = sorted({p.name: p for p in bg_files}.values(), key=lambda p: p.name)

choices = ["(nenhuma)"] + [p.name for p in bg_files]
st.session_state.setdefault("bg_file", choices[1] if len(choices) > 1 else "(nenhuma)")
st.session_state.setdefault("bg_darken", 25)
st.session_state.setdefault("bg_blur", 0)
st.session_state.setdefault("bg_fixed", True)
st.session_state.setdefault("bg_size", "cover")

bg_sel = st.sidebar.selectbox("Imagem", choices, index=choices.index(st.session_state["bg_file"]))
bg_darken = st.sidebar.slider("Escurecer overlay (%)", 0, 90, st.session_state["bg_darken"])
bg_blur = st.sidebar.slider("Desfoque (px)", 0, 20, st.session_state["bg_blur"])
bg_fixed = st.sidebar.checkbox("Fundo fixo", value=st.session_state["bg_fixed"])
bg_size  = st.sidebar.selectbox("Ajuste", ["cover", "contain"], index=(0 if st.session_state["bg_size"]=="cover" else 1))

st.session_state["bg_file"] = bg_sel
st.session_state["bg_darken"] = bg_darken
st.session_state["bg_blur"] = bg_blur
st.session_state["bg_fixed"] = bg_fixed
st.session_state["bg_size"] = bg_size

if bg_sel != "(nenhuma)":
    set_background(
        IMG_DIR / bg_sel,
        darken=bg_darken/100.0,
        blur_px=bg_blur,
        attach_fixed=bg_fixed,
        size_mode=bg_size,
    )

# ---------- Preferências rápidas (Mary) ----------
st.sidebar.markdown("---")
st.sidebar.subheader("🎚️ Preferências (Mary)")
if _char == "mary":
    def _idx(opt_list, value, default):
        try:
            return opt_list.index(value)
        except Exception:
            return default
    nivel_opts = ["sutil", "media", "alta"]
    ritmo_opts = ["lento", "normal", "rapido"]
    tam_opts   = ["curta","media","longa"]
    nivel = st.sidebar.selectbox("Nível sensual", nivel_opts,
                                 index=_idx(nivel_opts, str(facts.get("mary.pref.nivel_sensual","sutil")).lower(), 0))
    ritmo = st.sidebar.selectbox("Ritmo", ritmo_opts,
                                 index=_idx(ritmo_opts, str(facts.get("mary.pref.ritmo","lento")).lower(), 0))
    tam   = st.sidebar.selectbox("Tamanho da resposta", tam_opts,
                                 index=_idx(tam_opts, str(facts.get("mary.pref.tamanho_resposta","media")).lower(), 1))
    if st.sidebar.button("💾 Salvar preferências"):
        try:
            set_fact(user_key, "mary.pref.nivel_sensual", nivel, {"fonte":"prefs"})
            set_fact(user_key, "mary.pref.ritmo", ritmo, {"fonte":"prefs"})
            set_fact(user_key, "mary.pref.tamanho_resposta", tam, {"fonte":"prefs"})
            st.sidebar.success("Preferências salvas.")
        except Exception as e:
            st.sidebar.error(f"Falha ao salvar preferências: {e}")

# ---------- Janela de contexto ----------
st.sidebar.markdown("---")
st.sidebar.subheader("🧾 Janela de contexto")
st.session_state.setdefault("verbatim_ultimos", 10)
st.session_state["verbatim_ultimos"] = st.sidebar.slider(
    "Turnos verbatim (pares recentes)", 4, 18, st.session_state["verbatim_ultimos"]
)

# ---------- Carrega histórico ----------
_reload_history()

# ---------- Render histórico ----------
_last_role, _last_content = None, None
for role, content in st.session_state["history"]:
    if role == _last_role and content == _last_content:
        continue
    _last_role, _last_content = role, content
    with st.chat_message("user" if role == "user" else "assistant",
                         avatar=("💬" if role == "user" else "💚")):
        if role == "assistant":
            render_assistant_bubbles(content)
        else:
            st.markdown(content)

# ---------- LLM Ping ----------
with st.expander("🔧 Diagnóstico LLM"):
    if st.button("Ping modelo atual"):
        try:
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
            st.error(f"LLM ping: {e}")

# ---------- Helper de chamada segura ----------
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

# Chat input com chave fixa
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
                import traceback as _tb
                tb = _tb.format_exc()
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
        # Limpeza SEMPRE, mesmo em erro
        st.session_state["_pending_prompt"] = None
        st.session_state["_pending_auto"] = False
        st.session_state["_job_uid"] = None
        st.session_state["_is_generating"] = False

# ---------- Rodapé fixo: botões auxiliares ----------
footer = st.empty()
with footer:
    st.divider()
    cols = st.columns(2)
    with cols[0]:
        st.button(
            "🔁 Continuar",
            help="Prossegue a cena do ponto atual, sem mudar o local salvo.",
            on_click=lambda: st.session_state.__setitem__("_cont_clicked", True),
            use_container_width=True,
            key="continue_bottom",
        )
    with cols[1]:
        st.button(
            "🧾 Recap curto",
            help="Resumo telegráfico: nomes, locais/tempo atual, decisões e rumo do enredo.",
            on_click=lambda: st.session_state.__setitem__("_recap_clicked", True),
            use_container_width=True,
            key="recap_bottom",
        )
