# pages/3_Sala_Conjunta.py
from __future__ import annotations
import streamlit as st

from characters.registry import get_service, list_characters
from core.repositories import get_history_docs

st.set_page_config(page_title="Sala Conjunta", page_icon="👥", layout="centered")
st.title("👥 Sala Conjunta – Mary, Nerith, Laura e Adelle")

st.caption("Aqui vamos brincar de colocar todo mundo no mesmo ambiente, "
           "lendo o que já existe no Mongo e depois evoluir para interação em tempo real.")

user_id = st.text_input("👤 Usuário", value="Janio Donisete").strip() or "Janio Donisete"

chars = ["Mary", "Nerith", "Laura", "Adelle"]
cols = st.columns(len(chars))
ativos = []
for c, col in zip(chars, cols):
    with col:
        if st.checkbox(c, value=True):
            ativos.append(c)

st.markdown("---")

if not ativos:
    st.info("Selecione pelo menos uma personagem acima.")
    st.stop()

for nome in ativos:
    user_key = f"{user_id}::{nome.lower()}"
    st.subheader(f"💚 {nome}")
    try:
        docs = get_history_docs(user_key) or []
    except Exception as e:
        st.error(f"Erro ao ler histórico de {nome}: {e}")
        continue

    if not docs:
        st.caption("_Sem histórico salvo para esta dupla ainda._")
        continue

    for d in docs[-10:]:  # últimos 10 turnos
        u = (d.get("mensagem_usuario") or "").strip()
        a = (
            d.get(f"resposta_{nome.lower()}") or
            d.get("resposta") or
            d.get("assistant") or
            ""
        ).strip()
        if u:
            with st.chat_message("user", avatar="💬"):
                st.markdown(u)
        if a:
            with st.chat_message("assistant", avatar="💚"):
                st.markdown(a)
    st.markdown("---")
