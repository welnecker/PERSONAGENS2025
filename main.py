import streamlit as st
from characters.registry import get_service
from ui.sidebar_renderer import render_sidebar

def main():
    st.set_page_config(page_title="Roleplay App", layout="centered")
    st.title("Roleplay App")

    usuario = st.sidebar.text_input("Usuário", value="user1")
    personagem = st.sidebar.selectbox("Personagem", ["Mary","Laura","Nerith"])
    svc = get_service(personagem)

    usuario_key = usuario if svc.name.lower()=="mary" else f"{usuario}::{svc.name.lower()}"
    render_sidebar(svc, usuario_key)

    model = st.sidebar.text_input("Modelo", value="gpt-5")
    prompt = st.text_area("Mensagem", height=180, placeholder="Escreva sua mensagem...")

    if st.button("Enviar"):
        out = svc.gerar_resposta(usuario, prompt, model)
        st.write(out)

if __name__ == "__main__":
    main()

