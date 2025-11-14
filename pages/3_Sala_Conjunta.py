# pages/3_Sala_Conjunta.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict, Tuple

# ---- Imports internos do projeto ----
from characters.registry import get_service, list_characters
from core.repositories import get_history_docs
from core.service_router import list_models  # só para montar lista de modelos

# =============== CONFIG PÁGINA ===============
st.set_page_config(
    page_title="Sala Conjunta – Personagens 2025",
    page_icon="👥",
    layout="centered",
)

st.title("👥 Sala Conjunta – Mary, Nerith, Laura e Adelle")
st.caption(
    "Experimento de **meta-cena**: você fala uma vez e cada personagem responde "
    "a partir da sua própria memória no Mongo. A confusão é proposital. 😈"
)

st.markdown("---")

# =============== CONTROLES BÁSICOS ===============
# Usuário (mesmo esquema do main.py)
user_id = st.text_input("👤 Usuário", value=st.session_state.get("user_id", "Janio Donisete")).strip()
if not user_id:
    user_id = "Janio Donisete"
st.session_state["user_id"] = user_id  # manter coerência com main.py

# Lista oficial de personagens a partir do registry
all_chars = list_characters()  # deve retornar ["Mary", "Laura", "Adelle", "Nerith"]
# Vamos filtrar só as que nos interessam aqui (caso você adicione outras no futuro)
target_chars = [c for c in all_chars if c in ["Mary", "Laura", "Adelle", "Nerith"]]

default_sel = target_chars[:]  # todas marcadas
chars_sel = st.multiselect(
    "🎭 Personagens ativas nesta cena conjunta",
    options=target_chars,
    default=default_sel,
)
if not chars_sel:
    st.info("Selecione pelo menos uma personagem para continuar.")
    st.stop()

# =============== MODELOS ===============
FORCED_MODELS = [
    "deepseek/deepseek-chat-v3-0324",
    "anthropic/claude-3.5-haiku",
    "qwen/qwen3-max",
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
    "together/Qwen/Qwen2.5-72B-Instruct",
]

try:
    router_models = list_models(None) or []
except Exception:
    router_models = []

models_all = list(dict.fromkeys(router_models + FORCED_MODELS))
if not models_all:
    models_all = FORCED_MODELS

st.session_state.setdefault("model", models_all[0])
model_id = st.selectbox(
    "🧠 Modelo para todas as respostas",
    options=models_all,
    index=models_all.index(st.session_state["model"]) if st.session_state["model"] in models_all else 0,
)
st.session_state["model"] = model_id

st.markdown("---")

# =============== HISTÓRICO (LEITURA DO MONGO) ===============
st.subheader("📜 Últimos turnos de cada personagem")

for name in chars_sel:
    user_key = f"{user_id}::{name.lower()}"
    st.markdown(f"#### 💚 {name}")
    try:
        docs = get_history_docs(user_key) or []
    except Exception as e:
        st.error(f"Erro ao ler histórico de {name}: {e}")
        continue

    if not docs:
        st.caption("_Sem histórico salvo para esta dupla ainda._")
        st.markdown("---")
        continue

    # Mostra só os últimos 6 turnos (user + character)
    for d in docs[-6:]:
        u = (d.get("mensagem_usuario") or "").strip()
        a = (
            d.get(f"resposta_{name.lower()}")
            or d.get("resposta")
            or d.get("assistant")
            or ""
        ).strip()
        if u:
            with st.chat_message("user", avatar="💬"):
                st.markdown(u)
        if a:
            with st.chat_message("assistant", avatar="💚"):
                st.markdown(a)

    st.markdown("---")

# =============== CHAT CONJUNTO ===============
st.subheader("💥 Interação conjunta")

placeholder = "Fale algo que todas devam reagir…"
user_msg = st.chat_input(placeholder)

if user_msg:
    # Mostra sua fala uma vez
    with st.chat_message("user", avatar="💬"):
        st.markdown(user_msg)

    # Para cada personagem selecionada, chamamos o service.reply()
    for name in chars_sel:
        try:
            service = get_service(name)
        except Exception as e:
            with st.chat_message("assistant", avatar="⚠️"):
                st.markdown(f"Falha ao instanciar serviço de **{name}**: {e}")
            continue

        with st.spinner(f"Gerando resposta de {name}…"):
            try:
                # Cada service cuida de salvar no Mongo com sua chave própria
                txt = service.reply(user=user_id, model=model_id, prompt=user_msg)
            except TypeError:
                # fallback ultra simples se a assinatura for diferente
                txt = service.reply(user_id, model_id, user_msg)
            except Exception as e:
                txt = f"❌ Erro ao gerar resposta de {name}: {e}"

        avatar = "💚"
        label = f"**{name}**"
        with st.chat_message("assistant", avatar=avatar):
            st.markdown(f"{label}\n\n{txt}")
