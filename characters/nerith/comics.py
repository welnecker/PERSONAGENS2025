# ============================================================
# characters/nerith/comics.py — VERSÃO ATUALIZADA (Identidade Nerith + Estilo Quadrinho Adulto)
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
    # FAL: Dark Fantasy Flux (estilo HQ adulto / sombrio)
    "FAL • Dark Fantasy Flux": {
        "provider": "fal-ai",
        "model": "nerijs/dark-fantasy-illustration-flux",
        "sdxl": False,
        "size": "1024x1024",
    },
    # ✅ NOVO: SDXL-Lightning (fal-ai) — SDXL distilled, poucos steps, guidance ≤ 2.0
    "FAL • SDXL Lightning": {
        "provider": "fal-ai",
        "model": "ByteDance/SDXL-Lightning",
        "sdxl": True,
        "lightning": True,   # flag especial para UI/MAD e clamps
        "size": "1024x1024",
    },
    # SDXL base via Nscale
    "HF • SDXL (nscale)": {
        "provider": "huggingface-nscale",
        "model": "stabilityai/stable-diffusion-xl-base-1.0",
        "sdxl": True,
        "size": "1152x896",
    },
    # SDXL Refiner (pipeline 2 estágios)
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
    # FLUX nativo HF
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "sdxl": False,
        "size": "1024x1024",
    },
}

# ============================================================
# SDXL OFFICIAL RESOLUTIONS
# ============================================================
SDXL_SIZES = {
    "1152×896 (horizontal)": (1152, 896),
    "896×1152 (vertical)": (896, 1152),
    "1024×1024": (1024, 1024),
    "1216×832 (wide)": (1216, 832),
    "832×1216 (tall)": (832, 1216),
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

    # fal-ai e huggingface normal — funciona automaticamente
    return InferenceClient(token=token)

# ============================================================
# PROMPTS (SEM REFERÊNCIA A CELEBRIDADES)
# ============================================================
MAX_PROMPT_LEN = 1800
def _clean(s: str) -> str: return " ".join((s or "").split())
def _limit(s: str): return _clean(s)[:MAX_PROMPT_LEN]

# Traços faciais próprios da Nerith (sem citar pessoas reais)
FACE_POS = (
    "striking elven features, almond-shaped captivating emerald-green eyes, "
    "defined cheekbones, soft cat-eye eyeliner, full lips, "
    "mature confident allure, intense predatory gaze, elongated pointed elven ears"
)

# Corpo
BODY_POS = (
    "hourglass figure, athletic warrior body, defined flexible muscles, firm natural breasts, "
    "narrow waist, defined abdomen, wide hips, large rounded glutes, thick toned thighs"
)

# Anatomia negativa
ANATOMY_NEG = (
    "bad anatomy, deformed body, mutated body, malformed limbs, "
    "warped body, twisted spine, extra limbs, fused fingers, missing fingers, horns"
)

BODY_NEG = (
    "implants, sagging breasts, plastic body, barbie proportions, distorted waist"
)

# ✋ Bloqueio explícito de celebridades / likeness
CELEB_NEG = (
    "celebrity, celebrity lookalike, look alike, famous actress, "
    "face recognition match, portrait of a celebrity, sophia loren, monica bellucci, "
    "penelope cruz, gal gadot, angelina jolie"
)

# Sensualidade & SFW
SENSUAL_POS = (
    "subtle sensual posture, cinematic shadows caressing the skin, "
    "dramatic rimlight, implicit sensuality, alluring"
)
# MODIFICADO: Adicionado termos para evitar bloqueios NSFW
SENSUAL_NEG = "explicit, pornographic, nude, naked, topless, bottomless, nipples, pussy, explicit nudity, sexual, censored, text, watermark"

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
    "adult comic book illustration, dark mature tone, graphic novel art, dramatic chiaroscuro, "
    "rich blacks, heavy shadows, limited palette, gritty style"
)

# Negativo padrão com anti-celebridade
DEFAULT_NEG = f"{ANATOMY_NEG}, {BODY_NEG}, {TAIL_NEG}, {DOLL_NEG}, {CELEB_NEG}, watermark, text, signature"

# ============================================================
# PRESETS — com âncora de identidade da Nerith
# ============================================================
# MODIFICADO: Âncora de identidade atualizada para refletir a persona élfica.
IDENTITY_ANCHOR = (
    "Nerith, original character, female dark-elf warrior with matte cobalt-blue skin, "
    "long metallic silver hair, vivid emerald-green eyes, elongated pointed elven ears (no horns), "
    "solo subject, elegant yet fierce predatory presence"
)

PRESETS: Dict[str, Dict[str, str]] = {
    # ✅ NOVO PRESET: Focado em Quadrinho Adulto para Nerith
    "Nerith • Quadrinho Adulto": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "mature intensity, warrior presence, alluring pose"
        ),
        "negative": DEFAULT_NEG,
        "style": f"{COMIC_ADULT}",
    },
    
    # MODIFICADO: Ajustado para o novo padrão de identidade
    "Nerith • Dark Fantasy": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "dark fantasy aura, arcane energy, dramatic heavy shadows"
        ),
        "negative": DEFAULT_NEG,
        "style": (
            "dark fantasy illustration, heavy ink, deep shadows, "
            "misty atmosphere, gothic mood, dramatic highlights"
        ),
    },

    # MODIFICADO: Ajustado para o novo padrão de identidade
    "Nerith • Noir Comic": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "rain reflections, moody atmosphere"
        ),
        "negative": DEFAULT_NEG,
        "style": f"{COMIC_ADULT}, noir tone, cinematic shadows",
    },
}

# ============================================================
# BUILDER DE PROMPTS
# ============================================================
def build_prompts(preset, nsfw, framing, angle, pose, env):
    # Força nome da personagem no começo (alguns modelos ponderam mais o prefixo)
    pos = "Nerith; " + preset["positive"]
    neg = preset["negative"]
    style = preset["style"]

    # Cauda só fora de close-up
    if "close-up" not in framing:
        pos += ", " + TAIL_POS

    pos += f", {framing}, {angle}"
    if pose: pos += f", {pose}"
    if env: pos += f", scene: {env}"

    # MODIFICADO: Lógica de NSFW ajustada para ser mais segura
    if nsfw:
        # Se NSFW está ligado, focamos em sensualidade implícita e removemos o negativo explícito
        pos += f", {SENSUAL_POS}"
        final_neg = f"{neg}, {SENSUAL_NEG}" # Mantém o negativo forte para evitar bloqueios
    else:
        # Se NSFW está desligado, adicionamos um negativo forte contra qualquer sensualidade
        final_neg = f"{neg}, {SENSUAL_POS}, {SENSUAL_NEG}"

    prompt = _limit(f"{pos}, style: {style}, {INK_LINE_POS}, original character, no celebrity likeness")
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
        # MODIFICADO: Ordem dos modelos para priorizar o de quadrinhos
        prov_key = c1.selectbox("Modelo", list(PROVIDERS.keys()), index=0)
        cfg = PROVIDERS[prov_key]

        # MODIFICADO: Seleção automática de preset para o novo padrão
        preset_list = list(PRESETS.keys())
        default_preset = "Nerith • Quadrinho Adulto"
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
        # MODIFICADO: Toggle NSFW com texto mais claro
        nsfw = st.toggle("Liberar sensualidade implícita (SFW)", value=True, help="Gera imagens com poses e expressões sensuais, mas sem nudez explícita para evitar bloqueios.")
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
        # Steps / Guidance (com faixas por modelo)
        # -------------------------
        col_s, col_g = st.columns(2)
        if cfg.get("lightning"):
            # Lightning exige guidance ≤ 2.0 e poucos steps
            steps_default = 8
            guidance_default = 1.5
            steps = col_s.slider("Steps", 4, 24, steps_default)
            guidance = col_g.slider("Guidance", 0.0, 2.0, guidance_default)
        elif cfg.get("sdxl"):
            steps_default = 32
            guidance_default = 7.0
            steps = col_s.slider("Steps", 20, 60, steps_default)
            guidance = col_g.slider("Guidance", 2.0, 12.0, guidance_default)
        else:
            steps_default = 30
            guidance_default = 7.0
            steps = col_s.slider("Steps", 20, 60, steps_default)
            guidance = col_g.slider("Guidance", 2.0, 12.0, guidance_default)

        # MAD automático (tuning por modelo)
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

        # Clamp final para Lightning (evita 422 do backend)
        if cfg.get("lightning"):
            guidance = min(guidance, 2.0)

        # -------------------------
        # SDXL com REFINE (2 etapas) ou geração simples
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
