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
            "suspeitos": [],      # lista de dicts {"nome": "...", "assinatura": "...", "risco": "baixo/médio/alto"}
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

        suspeitos = ms.get("suspeitos") or []
        if suspeitos:
            with container.expander("Suspeitos detectados", expanded=True):
                for i, s in enumerate(suspeitos, start=1):
                    container.markdown(
                        f"- **{i}. {s.get('nome','?')}** • assinatura: `{s.get('assinatura','?')}` • risco: **{s.get('risco','?')}**"
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
