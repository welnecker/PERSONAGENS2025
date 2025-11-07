# ============================================================
# characters/nerith/comics.py — VERSÃO FINAL COM DARK FANTASY + SDXL LIGHTNING
# ============================================================
from __future__ import annotations
import os, io
from typing import Callable, List, Dict, Tuple, Optional
from PIL import Image
from huggingface_hub import InferenceClient
import streamlit as st

# ============================================================
# PROVIDERS — TODOS OS MODELOS DA NERITH
# ============================================================
PROVIDERS: Dict[str, Dict[str, str]] = {
    # FLUX nativo HF
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "sdxl": False,
        "size": "1024x1024",
    },

    # SDXL base via Nscale
    "HF • SDXL (nscale)": {
        "provider": "huggingface-nscale",
        "model": "stabilityai/stable-diffusion-xl-base-1.0",
        "sdxl": True,
        "size": "1152x896",
    },

    # SDXL Refiner
    "HF • SDXL (nscale + Refiner)": {
        "provider": "huggingface-nscale",
        "model": "stabilityai/stable-diffusion-xl-refiner-1.0",
        "sdxl": True,
        "refiner": True,
        "size": "1152x896",
    },

    # FAL: Stable Image Ultra
    "FAL • Stable Image Ultra": {
        "provider": "fal-ai",
        "model": "stabilityai/stable-image-ultra",
        "sdxl": False,
        "size": "1024x1024",
    },

    # FAL: Dark Fantasy Flux (novo)
    "FAL • Dark Fantasy Flux": {
        "provider": "fal-ai",
        "model": "nerijs/dark-fantasy-illustration-flux",
        "sdxl": False,
        "size": "1024x1024",
    },

    # ✅ NOVO: SDXL-Lightning (fal-ai) — SDXL distilled para poucos steps
    "FAL • SDXL Lightning": {
        "provider": "fal-ai",
        "model": "ByteDance/SDXL-Lightning",
        "sdxl": True,         # continua sendo SDXL, só que lightning/distilled
        "lightning": True,    # flag para ajustes de steps/guidance
        "size": "1024x1024",
    },
}

# ============================================================
# SDXL OFFICIAL RESOLUTIONS
# ============================================================
SDXL_SIZES = {
    "1152×896 (horizontal)": (1152, 896),
    "896×1152 (vertical)": (896, 1152),
    "1216×832 (wide)": (1216, 832),
    "832×1216 (tall)": (832, 1216),
    "1024×1024": (1024, 1024),
}

# ============================================================
# CLIENTES HF / NSCALE / FAL
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

    # fal-ai, huggingface normal — funciona automaticamente
    return InferenceClient(token=token)

# ============================================================
# PROMPTS
# ============================================================
MAX_PROMPT_LEN = 1800
def _clean(s: str) -> str: return " ".join((s or "").split())
def _limit(s: str): return _clean(s)[:MAX_PROMPT_LEN]

# BLOCO FACIAL — Sophia Loren
FACE_POS = (
    "face like a young Sophia Loren, almond-shaped sultry eyes, "
    "defined cheekbones, soft cat-eye eyeliner, full lips, "
    "mature confident allure, subtle intensity"
)

# Corpo
BODY_POS = (
    "hourglass figure, soft athletic tone, firm natural breasts, "
    "narrow waist, defined abdomen, high-set rounded glutes"
)

# Anatomia negativa
ANATOMY_NEG = (
    "bad anatomy, deformed body, mutated body, malformed limbs, "
    "warped body, twisted spine, extra limbs, fused fingers, missing fingers"
)

BODY_NEG = (
    "balloon breasts, implants, sagging breasts, torpedo breasts, "
    "plastic body, barbie proportions, distorted waist"
)

# Sensualidade & SFW
SENSUAL_POS = (
    "subtle sensual posture, cinematic shadows caressing the skin, "
    "dramatic rimlight, implicit sensuality"
)
SENSUAL_NEG = "explicit, pornographic, nude, censored, text, watermark"

# Cauda biomecânica
TAIL_POS = (
    "a single biomechanical blade-tail fused to the spine, silver metal, "
    "sharp edges, blue glowing energy vein"
)
TAIL_NEG = "furry tail, fleshy tail, animal tail, penis tail, detached tail"

# Anti-boneca
DOLL_NEG = (
    "doll, barbie, plastic skin, CGI skin, beauty-filter, "
    "uncanny-valley, over-smooth skin, poreless skin, wax figure"
)

# Textura HQ
INK_LINE_POS = (
    "inked line art, strong outlines, cel shading, halftone dots, "
    "cross-hatching, textured paper grain, gritty shadows"
)

# Estilo Comic Adulto
COMIC_ADULT = (
    "adult comic illustration, dark mature tone, dramatic chiaroscuro, "
    "rich blacks, heavy shadows, limited palette"
)

DEFAULT_NEG = f"{ANATOMY_NEG}, {BODY_NEG}, {TAIL_NEG}, {DOLL_NEG}, watermark, text, signature"

# ============================================================
# PRESETS — INCLUINDO DARK FANTASY
# ============================================================
PRESETS: Dict[str, Dict[str, str]] = {
    # FLUX HQ clássico
    "FLUX • Nerith HQ": {
        "positive": (
            f"{FACE_POS}, metallic silver hair, green eyes, dark-elf, "
            f"{BODY_POS}, blue-slate skin, {INK_LINE_POS}, elegant posture"
        ),
        "negative": DEFAULT_NEG,
        "style": "masterpiece comic art, neon rimlight, high contrast",
    },

    # SDXL Quadrinho Adulto
    "SDXL • Nerith Comic (Adulto)": {
        "positive": (
            f"{FACE_POS}, metallic silver hair, deep green eyes, "
            f"{BODY_POS}, matte blue-slate skin, {INK_LINE_POS}, "
            "mature intensity, warrior presence"
        ),
        "negative": DEFAULT_NEG,
        "style": f"{COMIC_ADULT}",
    },

    # SDXL Noir
    "SDXL • Nerith Noir Comic": {
        "positive": (
            f"{FACE_POS}, jade eyes, silver hair, "
            f"{BODY_POS}, {INK_LINE_POS}, rain reflections, moody atmosphere"
        ),
        "negative": DEFAULT_NEG,
        "style": f"{COMIC_ADULT}, noir tone, cinematic shadows",
    },

    # Dark Fantasy (Flux / Fal-ai)
    "FLUX • Nerith Dark Fantasy": {
        "positive": (
            f"{FACE_POS}, metallic silver hair flowing dramatically, "
            "deep green eyes glowing faintly, blue-slate dark-elf skin, "
            f"{BODY_POS}, {INK_LINE_POS}, "
            "dark fantasy aura, arcane energy, dramatic heavy shadows"
        ),
        "negative": DEFAULT_NEG,
        "style": (
            "dark fantasy illustration, heavy ink, deep shadows, "
            "misty atmosphere, gothic mood, dramatic highlights"
        ),
    },
}

# ============================================================
# BUILDER DE PROMPTS
# ============================================================
def build_prompts(preset, nsfw, framing, angle, pose, env):
    pos = preset["positive"]
    neg = preset["negative"]
    style = preset["style"]

    # Cauda só fora de close-up
    if "close-up" not in framing:
        pos += ", " + TAIL_POS

    pos += f", {framing}, {angle}"

    if pose: pos += f", {pose}"
    if env: pos += f", scene: {env}"

    if nsfw:
        final_style = style
        final_neg = f"{neg}, {SENSUAL_NEG}"
    else:
        final_style = f"{style}, soft cinematic elegance"
        final_neg = f"{neg}, {SENSUAL_POS}"

    prompt = _limit(f"{pos}, style: {final_style}, {INK_LINE_POS}")
    negative = _limit(final_neg)
    return prompt, negative

# ============================================================
# UI PRINCIPAL
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

        # -------------------------
        # Modelo e Predefinição
        # -------------------------
        c1, c2 = st.columns(2)
        prov_key = c1.selectbox("Modelo", list(PROVIDERS.keys()), index=0)
        cfg = PROVIDERS[prov_key]

        # Seleção automática
        if prov_key == "FAL • Dark Fantasy Flux":
            default_preset = "FLUX • Nerith Dark Fantasy"
        elif cfg.get("sdxl"):
            default_preset = "SDXL • Nerith Comic (Adulto)"
        else:
            default_preset = "FLUX • Nerith HQ"

        preset_list = list(PRESETS.keys())
        idx = preset_list.index(default_preset) if default_preset in preset_list else 0
        preset_name = c2.selectbox("Preset", preset_list, index=idx)
        preset = PRESETS[preset_name]

        # -------------------------
        # Direção da cena
        # -------------------------
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

        # -------------------------
        # Resolução
        # -------------------------
        if cfg.get("sdxl"):
            sz = st.selectbox("📐 Resolução SDXL", list(SDXL_SIZES.keys()), index=0)
            width, height = SDXL_SIZES[sz]
        else:
            width, height = map(int, cfg["size"].split("x"))

        # -------------------------
        # Steps / Guidance
        # -------------------------
        col_s, col_g = st.columns(2)

        # Defaults sensatos por modelo
        if cfg.get("lightning"):
            # Lightning é distilled: poucos steps e guidance baixo
            steps_default = 8
            guidance_default = 3.0
        elif cfg.get("sdxl"):
            steps_default = 32
            guidance_default = 7.0
        else:
            steps_default = 30
            guidance_default = 7.0

        steps = col_s.slider("Steps", 2, 60, steps_default)
        guidance = col_g.slider("Guidance", 1.0, 12.0, guidance_default)

        # MAD automático
        if mad:
            if cfg.get("lightning"):
                # afinado para Lightning (rápido, menos artefatos)
                guidance = 2.5
                steps = max(6, steps)   # 6–8 costuma ser ótimo
            elif prov_key == "FAL • Dark Fantasy Flux":
                guidance = 6.3
                steps = max(30, steps)
            elif cfg.get("sdxl"):
                guidance = 5.6
                steps = max(32, steps)
            else:
                guidance = 7.2
                steps = max(26, steps)

        # -------------------------
        # Botão
        # -------------------------
        go = st.button("Gerar Painel 🎨", use_container_width=True)
        if not go:
            return

        prompt, negative = build_prompts(preset, nsfw, framing, angle, pose, env)

        # Anti-Barbie adicional (segurança)
        if mad:
            negative += (
                ", barbie-doll, plastic texture, CGI texture, over-smooth shader, "
                "beauty-filtered skin, poreless skin"
            )

        with st.expander("Prompts finais"):
            st.code(prompt)
            st.code(negative)

        # -------------------------
        # Client
        # -------------------------
        client = _get_client(cfg["provider"])
        st.info(f"✅ Provider: {cfg['provider']} — Modelo: {cfg['model']} ({width}×{height}, steps={steps}, guidance={guidance})")

        # -------------------------
        # SDXL com REFINE
        # -------------------------
        if cfg.get("refiner"):
            with st.spinner("Etapa 1: SDXL Base..."):
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
            with st.spinner("Etapa 2: Refiner..."):
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

        # -------------------------
        # Render final
        # -------------------------
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
