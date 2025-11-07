# characters/nerith/comics.py — UI principal
from __future__ import annotations
import io
from typing import Callable, List, Dict, Optional
from PIL import Image
import streamlit as st

from .providers import PROVIDERS, SDXL_SIZES, get_client, parse_size
from .presets import PRESETS, DEFAULT_NEG
from .prompt_builder import build_prompts, qwen_prompt_fix

def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    title: str = "🎞️ Diretor de Arte (Nerith)",
    ui=None,
    key_prefix: str = ""
) -> None:
    ui = ui or st
    key_prefix = key_prefix or "nerith_comics"

    try:
        st.markdown(f"### {title}")

        # =======================
        # Modelo + Preset
        # =======================
        c1, c2 = st.columns(2)
        prov_key = c1.selectbox("Modelo", list(PROVIDERS.keys()), index=0, key=f"{key_prefix}_model")
        cfg = PROVIDERS[prov_key]

        # Seleção automática de preset
        if prov_key == "FAL • Dark Fantasy Flux":
            default_preset = "FLUX • Nerith Dark Fantasy"
        elif cfg.get("qwen"):
            default_preset = "Qwen • Nerith Realism Comic"
        elif cfg.get("sdxl"):
            default_preset = "SDXL • Nerith Comic (Adulto)"
        else:
            default_preset = "FLUX • Nerith HQ"

        preset_list = list(PRESETS.keys())
        idx = preset_list.index(default_preset) if default_preset in preset_list else 0
        preset_name = c2.selectbox("Preset", preset_list, index=idx, key=f"{key_prefix}_preset")
        preset = PRESETS[preset_name]

        # =======================
        # Direção da Cena
        # =======================
        st.markdown("---")
        st.subheader("Direção da Cena")

        col_f, col_a = st.columns(2)
        framing_map = {
            "Retrato (close-up)": "close-up portrait",
            "Meio corpo": "medium shot",
            "Corpo inteiro": "full body",
        }
        framing = framing_map[col_f.selectbox("Enquadramento", list(framing_map.keys()), index=2, key=f"{key_prefix}_frame")]

        angle_map = {
            "Frente": "front view",
            "Lado": "side view",
            "Costas": "back view",
            "Três quartos": "three-quarter view",
        }
        angle = angle_map[col_a.selectbox("Ângulo", list(angle_map.keys()), index=3, key=f"{key_prefix}_angle")]

        with st.expander("Direção de Arte (Opcional)"):
            pose = st.text_input("Pose / Ação", key=f"{key_prefix}_pose")
            env = st.text_input("Ambiente / Cenário", key=f"{key_prefix}_env")

        st.markdown("---")
        nsfw = st.toggle("Liberar sensualidade implícita", value=True, key=f"{key_prefix}_nsfw")
        mad = st.toggle("🔥 Modo Automático Anti-Deformações", value=True, key=f"{key_prefix}_mad")

        # =======================
        # Resolução
        # =======================
        if cfg.get("sdxl"):
            sz = st.selectbox("📐 Resolução SDXL", list(SDXL_SIZES.keys()), index=0, key=f"{key_prefix}_size")
            width, height = SDXL_SIZES[sz]
        else:
            width, height = parse_size(str(cfg["size"]))

        # =======================
        # Steps / Guidance (faixas por modelo)
        # =======================
        col_s, col_g = st.columns(2)
        if cfg.get("lightning"):
            steps = col_s.slider("Steps", 4, 24, 8, key=f"{key_prefix}_steps")
            guidance = col_g.slider("Guidance", 0.0, 2.0, 1.5, key=f"{key_prefix}_guidance")
        elif cfg.get("sdxl"):
            steps = col_s.slider("Steps", 20, 60, 32, key=f"{key_prefix}_steps")
            guidance = col_g.slider("Guidance", 2.0, 12.0, 7.0, key=f"{key_prefix}_guidance")
        else:
            steps = col_s.slider("Steps", 20, 60, 30, key=f"{key_prefix}_steps")
            guidance = col_g.slider("Guidance", 2.0, 12.0, 7.0, key=f"{key_prefix}_guidance")

        # Ajustes Qwen básicos
        if cfg.get("qwen"):
            steps = min(steps, 24)
            guidance = min(guidance, 6.0)

        # MAD — tuning por modelo
        if mad:
            if cfg.get("lightning"):
                guidance = min(1.8, guidance)
                steps = max(6, min(12, steps))
            elif prov_key == "FAL • Dark Fantasy Flux":
                guidance = 6.3
                steps = max(30, steps)
            elif cfg.get("sdxl"):
                guidance = 5.6
                steps = max(32, steps)
            elif cfg.get("qwen"):
                guidance = min(5.5, guidance)
                steps = max(18, steps)
            else:
                guidance = 7.2
                steps = max(26, steps)

        # =======================
        # Botão
        # =======================
        go = st.button("Gerar Painel 🎨", use_container_width=True, key=f"{key_prefix}_go")
        if not go:
            return

        # Prompts
        prompt, negative = build_prompts(preset, nsfw, framing, angle, pose, env)

        # Anti-barbie adicional
        if mad:
            negative += ", barbie-doll, plastic texture, CGI texture, over-smooth shader, beauty-filtered skin, poreless skin"

        # Fix específico Qwen
        if cfg.get("qwen"):
            prompt = qwen_prompt_fix(prompt)
            negative += ", over-smooth skin, plastic face, barbie face"

        with st.expander("Prompts finais"):
            st.code(prompt)
            st.code(negative)

        # Cliente
        client = get_client(str(cfg["provider"]))
        st.info(f"✅ Provider: {cfg['provider']} — Modelo: {cfg['model']} ({width}×{height}, steps={steps}, guidance={guidance})")

        # Clamp final para Lightning (evita 422)
        if cfg.get("lightning"):
            guidance = min(guidance, 2.0)

        # Geração
        if cfg.get("refiner"):
            # Passo 1: SDXL Base
            with st.spinner("Etapa 1: SDXL Base..."):
                base_img = client.text_to_image(
                    prompt=prompt,
                    model="stabilityai/stable-diffusion-xl-base-1.0",
                    negative_prompt=negative,
                    width=width,
                    height=height,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                )
            # Passo 2: Refiner (usa imagem do passo 1)
            with st.spinner("Etapa 2: Refiner..."):
                img_data = client.text_to_image(
                    prompt=prompt,
                    model="stabilityai/stable-diffusion-xl-refiner-1.0",
                    negative_prompt=negative,
                    image=base_img,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                )
        else:
            with st.spinner("Gerando painel..."):
                img_data = client.text_to_image(
                    prompt=prompt,
                    model=str(cfg["model"]),
                    negative_prompt=negative,
                    width=width,
                    height=height,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                )

        img = Image.open(io.BytesIO(img_data)) if isinstance(img_data, (bytes, bytearray)) else img_data

        st.image(img, caption=f"Preset: {preset_name}", use_column_width=True)

        buf = io.BytesIO()
        img.save(buf, "PNG")
        st.download_button(
            "⬇️ Baixar PNG",
            data=buf.getvalue(),
            file_name="nerith_comic.png",
            mime="image/png",
            key=f"{key_prefix}_dl",
        )

    except Exception as e:
        st.error(f"Erro: {e}")
        st.exception(e)
