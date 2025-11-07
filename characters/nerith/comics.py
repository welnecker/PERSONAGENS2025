# ============================================================
# characters/nerith/comics.py — VERSÃO FINAL COMPLETA (Comic Adulto + Sophia Loren)
# ============================================================
from __future__ import annotations
import os, io
from typing import Callable, List, Dict, Tuple, Optional
from PIL import Image
from huggingface_hub import InferenceClient
import streamlit as st

# ============================================================
# PROVIDERS (Modelos disponíveis)
# ============================================================
PROVIDERS: Dict[str, Dict[str, str]] = {
    # FLUX nativo HF
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "sdxl": False,
        "size": "1024x1024",
    },

    # SDXL via Nscale (GPU remota HF)
    "HF • SDXL (nscale)": {
        "provider": "huggingface-nscale",
        "model": "stabilityai/stable-diffusion-xl-base-1.0",
        "sdxl": True,
        "size": "1152x896",
    },

    # SDXL Refiner (opcional)
    "HF • SDXL (nscale + Refiner)": {
        "provider": "huggingface-nscale",
        "model": "stabilityai/stable-diffusion-xl-refiner-1.0",
        "sdxl": True,
        "refiner": True,
        "size": "1152x896",
    },

    # Fal
    "FAL • Stable Image Ultra": {
        "provider": "fal-ai",
        "model": "stabilityai/stable-image-ultra",
        "sdxl": False,
        "size": "1024x1024",
    },
}

# ============================================================
# SDXL OFFICIAL RESOLUTIONS
# ============================================================
SDXL_SIZES = {
    "1024×1024": (1024, 1024),
    "1152×896 (horizontal)": (1152, 896),
    "896×1152 (vertical)": (896, 1152),
    "1216×832 (wide)": (1216, 832),
    "832×1216 (tall)": (832, 1216),
}

# ============================================================
# TOKENS / CLIENTS
# ============================================================
def _get_hf_token() -> str:
    tok = (
        str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or
        str(st.secrets.get("HF_TOKEN", "")) or
        os.environ.get("HUGGINGFACE_API_KEY", "") or
        os.environ.get("HF_TOKEN", "")
    )
    if not tok.strip():
        raise RuntimeError("⚠️ Faltando token: defina HUGGINGFACE_API_KEY ou HF_TOKEN")
    return tok.strip()


def _get_client(provider: Optional[str]) -> InferenceClient:
    pv = (provider or "").lower().strip()
    token = _get_hf_token()

    if pv in ("huggingface-nscale", "nscale", "hf-nscale"):
        return InferenceClient(provider="nscale", api_key=token)

    return InferenceClient(token=token)

# ============================================================
# PROMPTS (blocos reutilizáveis)
# ============================================================
MAX_PROMPT_LEN = 1800
def _clean(s: str) -> str:
    return " ".join((s or "").split())
def _limit(s: str) -> str:
    return _clean(s)[:MAX_PROMPT_LEN]

# Anatomia / corpo
ANATOMY_NEG = (
    "bad anatomy, deformed, mutated, malformed, dislocated, twisted torso, "
    "broken spine, fused fingers, missing fingers, extra fingers, extra limbs"
)
FACE_POS = (
    "face like a young Sophia Loren, sultry almond-shaped eyes, defined cheekbones, "
    "soft cat-eye eyeliner, full lips, confident mature allure"
)
BODY_POS = "hourglass figure, soft muscle tone, firm natural breasts, narrow waist, round glutes"
BODY_NEG = "balloon breasts, implants, disfigured body, warped waist, mutilated skin"

# Sensualidade
SENSUAL_POS = (
    "soft cinematic shadows, light caressing skin, subtle sensual posture, "
    "moody highlights, silhouette emphasis, implicit sensuality"
)
SENSUAL_NEG = "text, watermark, censored, lowres, jpeg artifacts"

# Cauda biomecânica
TAIL_POS = (
    "a sleek biomechanical blade-tail fused to spine, silver metal, blue glowing energy vein"
)
TAIL_NEG = "furry tail, fleshy tail, animal tail, penis tail, detached tail"

# Anti-boneca / textura de HQ
DOLL_NEG = (
    "doll, plastic, toy-like, mannequin, wax figure, uncanny valley, "
    "over-smooth skin, airbrushed skin, CGI look, glossy skin, rubber skin"
)
INK_LINE_POS = (
    "inked line art, hand-inked contours, clean bold outlines, cel shading, "
    "subtle paper grain, halftone texture, fine cross-hatching, film grain"
)
COMIC_ADULT_STYLE = (
    "adult comic illustration, bande dessinee vibe, dramatic chiaroscuro, "
    "rich blacks, controlled color palette"
)

DEFAULT_NEG = f"{ANATOMY_NEG}, {BODY_NEG}, {TAIL_NEG}, {DOLL_NEG}, watermark, text, signature, logo"

# ============================================================
# PRESETS (Comic Adulto + Sophia Loren)
# ============================================================
PRESETS: Dict[str, Dict[str, str]] = {
    # FLUX: HQ nítido
    "FLUX • Nerith HQ": {
        "positive": (
            f"{FACE_POS}, metallic silver hair, piercing green eyes, "
            "dark-elf, luminous blue-slate skin, "
            f"{BODY_POS}, {INK_LINE_POS}, elegant, regal posture"
        ),
        "negative": f"{DEFAULT_NEG}",
        "style": "masterpiece comic art, sharp edges, neon rimlight, high contrast",
    },

    # SDXL — Quadrinho Adulto (principal)
    "SDXL • Nerith Comic (Adulto)": {
        "positive": (
            f"{FACE_POS}, metallic silver hair with subtle speculars, deep green eyes, "
            "dark-elf warrior, matte blue-slate skin, "
            f"{BODY_POS}, {INK_LINE_POS}, confident mature expression"
        ),
        "negative": f"{DEFAULT_NEG}",
        "style": f"{COMIC_ADULT_STYLE}",
    },

    # SDXL — Noir (dramático)
    "SDXL • Nerith Noir Comic": {
        "positive": (
            f"{FACE_POS}, silver hair, jade green eyes, "
            "dark-elf, matte blue-slate skin, "
            f"{BODY_POS}, {INK_LINE_POS}, dynamic pose, moody atmosphere"
        ),
        "negative": f"{DEFAULT_NEG}",
        "style": f"{COMIC_ADULT_STYLE}, deep contrast, moody lighting, rain reflections",
    },
}

def build_prompts(preset, nsfw_on, framing, angle, pose, env):
    base_pos = preset["positive"]
    base_neg = preset["negative"]
    style = preset["style"]

    final_pos = base_pos
    if "close-up" not in framing:
        final_pos += ", " + TAIL_POS

    final_pos += f", {framing}, {angle}"
    if pose:
        final_pos += f", {pose}"
    if env:
        final_pos += f", scene: {env}"

    if nsfw_on:
        final_style = style
        final_neg = f"{base_neg}, {SENSUAL_NEG}"
    else:
        final_style = f"{COMIC_ADULT_STYLE}, cinematic, elegant, dramatic lighting"
        final_neg = f"{base_neg}, {SENSUAL_POS}"

    # Garantir textura de HQ para quebrar look plástico
    prompt = _limit(f"{final_pos}, style: {final_style}, {INK_LINE_POS}")
    negative = _limit(final_neg)
    return prompt, negative

# ============================================================
# UI MAIN BUTTON
# ============================================================
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    title="🎞️ Diretor de Arte (Nerith)",
    ui=None,
    key_prefix=""
):
    ui = ui or st
    key_prefix = key_prefix or "nerith_comics"

    try:
        st.markdown(f"### {title}")

        # -----------------------------
        # Modelo + Preset
        # -----------------------------
        c1, c2 = st.columns(2)
        prov_key = c1.selectbox("Modelo", list(PROVIDERS.keys()), index=0)
        cfg = PROVIDERS[prov_key]

        # Seleção automática de preset por modelo
        if cfg.get("sdxl"):
            default_preset = "SDXL • Nerith Comic (Adulto)"
        else:
            default_preset = "FLUX • Nerith HQ"

        preset_list = list(PRESETS.keys())
        # Segurança: se preset não existir, cai para index 0
        idx = preset_list.index(default_preset) if default_preset in preset_list else 0
        preset_name = c2.selectbox("Preset", preset_list, index=idx)
        preset = PRESETS[preset_name]

        # -----------------------------
        # Direção de cena
        # -----------------------------
        st.markdown("---")
        st.subheader("Direção da Cena")

        col_f, col_a = st.columns(2)
        framing_map = {
            "Retrato (close-up)": "close-up portrait",
            "Meio corpo": "medium shot",
            "Corpo inteiro": "full body",
        }
        framing = framing_map[col_f.selectbox("Enquadramento", list(framing_map.keys()), index=2)]

        angle_map = {
            "Frente": "front view",
            "Lado": "side view",
            "Costas": "back view",
            "Três quartos": "three-quarter view",
        }
        angle = angle_map[col_a.selectbox("Ângulo", list(angle_map.keys()), index=3)]

        with st.expander("Direção de Arte (Opcional)"):
            pose = st.text_input("Pose / Ação")
            env = st.text_input("Ambiente / Cenário")

        st.markdown("---")
        nsfw = st.toggle("Liberar sensualidade implícita", value=True)
        mad = st.toggle("🔥 Modo Automático Anti-Deformações", value=True)

        # -----------------------------
        # SDXL Sizes
        # -----------------------------
        if cfg.get("sdxl"):
            size_label = st.selectbox("📐 Resolução SDXL", list(SDXL_SIZES.keys()), index=1)
            width, height = SDXL_SIZES[size_label]
        else:
            width, height = map(int, cfg["size"].split("x"))

        # -----------------------------
        # Ajustes técnicos
        # -----------------------------
        col_s, col_g = st.columns(2)
        steps = col_s.slider("Steps", 20, 60, 30)
        guidance = col_g.slider("Guidance", 3.0, 12.0, 7.0)

        # Ajuste automático MAD (afinando para HQ adulto)
        if mad:
            if cfg.get("sdxl"):
                # Guidance menor reduz “plástico” no SDXL
                guidance = 5.5
                steps = max(30, steps)
            else:
                guidance = 7.0
                steps = max(26, steps)

        # Gerar
        go = st.button("Gerar Painel 🎨", use_container_width=True)
        if not go:
            return

        prompt, negative = build_prompts(preset, nsfw, framing, angle, pose, env)

        # Reforço extra anti-boneca no MAD
        if mad and cfg.get("sdxl"):
            negative += (
                ", plastic doll face, beauty filter, poreless skin, hyper-smooth shader, "
                "overprocessed, waxy highlights"
            )
        elif mad and not cfg.get("sdxl"):
            negative += ", lowres edges, messy outlines, watercolor bleed"

        with st.expander("Prompts finais"):
            st.code(prompt)
            st.code(negative)

        # Client
        client = _get_client(cfg["provider"])
        st.info(f"✅ Usando provider: {cfg['provider']} — modelo: {cfg['model']} ({width}×{height}, steps={steps}, guidance={guidance})")

        # SDXL + Refiner
        if cfg.get("refiner"):
            with st.spinner("Etapa 1: Gerando imagem base SDXL..."):
                latent = client.text_to_image(
                    prompt=prompt,
                    model="stabilityai/stable-diffusion-xl-base-1.0",
                    negative_prompt=negative,
                    width=width,
                    height=height,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                    output_type="latent",
                )
            with st.spinner("Etapa 2: Refinando..."):
                img_data = client.text_to_image(
                    prompt=prompt,
                    model="stabilityai/stable-diffusion-xl-refiner-1.0",
                    negative_prompt=negative,
                    image=latent,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                )
        else:
            with st.spinner("Gerando painel..."):
                img_data = client.text_to_image(
                    prompt=prompt,
                    model=cfg["model"],
                    negative_prompt=negative,
                    width=width,
                    height=height,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                )

        # Converter
        img = Image.open(io.BytesIO(img_data)) if isinstance(img_data, (bytes, bytearray)) else img_data

        st.image(img, caption=f"Preset: {preset_name}", use_column_width=True)

        buf = io.BytesIO()
        img.save(buf, "PNG")
        st.download_button(
            "⬇️ Baixar PNG",
            data=buf.getvalue(),
            file_name="nerith_comic.png",
            mime="image/png"
        )

    except Exception as e:
        st.error(f"Erro: {e}")
        st.exception(e)
