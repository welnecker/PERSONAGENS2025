# characters/nerith/comics.py
from __future__ import annotations
import os, io, re
from typing import Callable, List, Dict, Tuple
from PIL import Image
from huggingface_hub import InferenceClient
import streamlit as st

# Tenta importar o wrapper do USO e define uma flag de disponibilidade.
# Esta abordagem é mais robusta para diferentes estruturas de projeto.
try:
    import uso_wrapper
    USO_AVAILABLE = True
except ImportError:
    USO_AVAILABLE = False
    # O aviso será mostrado na UI se o wrapper não for encontrado.

# ======================
# Config / Providers
# ======================
# Define a base de provedores. O USO será adicionado dinamicamente se disponível.
PROVIDERS: Dict[str, Dict[str, str]] = {
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "size": "1024x1024",
    },
}

# Lógica corrigida: Adiciona o provedor USO à lista SE ele estiver disponível.
if USO_AVAILABLE:
    PROVIDERS["USO (FLUX.1-dev)"] = {
        "provider": "uso_local",
        "model": "bytedance-research/USO",
        "size": "1024x1024",
    }

# ======================
# Token / Client
# ======================
def _get_hf_token() -> str:
    tok = (str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or str(st.secrets.get("HF_TOKEN", "")))
    if not tok:
        tok = os.environ.get("HUGGINGFACE_API_KEY", "") or os.environ.get("HF_TOKEN", "")
    if not (tok or "").strip():
        raise RuntimeError("Defina HUGGINGFACE_API_KEY (ou HF_TOKEN) em st.secrets ou variável de ambiente.")
    return tok.strip()

@st.cache_resource
def _hf_client() -> InferenceClient:
    return InferenceClient(token=_get_hf_token())

# ======================
# Utilitários de Prompt
# ======================
MAX_PROMPT_LEN = 1800

def _squash_spaces(s: str) -> str:
    return " ".join((s or "").split())

def _fit_to_limit(text: str, max_len: int = MAX_PROMPT_LEN) -> str:
    t = _squash_spaces(text or "")
    return t[:max_len] if len(t) > max_len else t

# ======================
# Blocos de Prompt (Positivos e Negativos)
# ======================
ANATOMY_NEG = "bad anatomy, deformed, mutated, malformed, dislocated, broken spine, twisted torso, extra limbs, extra fingers, fused fingers, missing fingers, missing legs, cropped feet"
DUPLICATE_NEG = "two people, two girls, duplicate, twin, second person, extra person, clone, copy, siamese, overlapping bodies"
HORN_NEG = "horns, horn, antlers, head spikes, forehead protrusions, demon horns, ram horns, goat horns"
FACE_POS = "face like a young Sophia Loren, high cheekbones, almond-shaped captivating eyes, full lips, confident expression"
BODY_POS = "athletic hourglass figure, toned yet feminine, full firm natural teardrop breasts, flat defined abdomen, narrow waist, round high-set glutes"
BODY_NEG = "balloon breasts, sphere boobs, torpedo breasts, implants, uneven breasts, collapsed chest, distorted abdomen, square butt, exaggerated butt, wasp waist"
SFW_NEG = "explicit, pornographic, sexual, nude, naked"
SENSUAL_POS = "body draped in shadow and light, skin glistening under soft light, form-fitting silk robe partly open, lounging seductively, intimate atmosphere, alluring posture, moody soft shadows, implicit sensuality"
SENSUAL_NEG = "fully clothed, modest, chaste, asexual, text, watermark, signature"
TAIL_POS = ("a single biomechanical blade-tail made of gleaming silver metal with razor-sharp edges and subtle blue energy lines, anatomically fused to her lower back, originating from the sacrum and coccyx, emerging seamlessly from her skin above the gluteal crease, clearly not a furry or fleshy tail")
TAIL_NEG = "phallic tail, penis-like tail, tail shaped like a limb, tail fused to leg, detached tail, floating tail, furry tail, fleshy tail, animal tail"

# ===================================================
# PRESETS
# ===================================================
_DEFAULT_PRESETS: Dict[str, Dict[str, str]] = {
    "Nerith • Boudoir (Sophia Face)": {
        "positive": f"({FACE_POS}), female dark-elf, blue-slate luminous skin, metallic silver long hair, piercing green eyes, elongated pointed elven ears (no horns), solo subject, {BODY_POS}",
        "negative": ", ".join([HORN_NEG, DUPLICATE_NEG, ANATOMY_NEG, BODY_NEG, TAIL_NEG]),
        "style": f"masterpiece comic art, bold ink outlines, cel shading, dramatic lighting, {SENSUAL_POS}",
    },
    "Nerith • Caçadora (Sophia Face)": {
        "positive": f"({FACE_POS}), female dark-elf, blue-slate skin, metallic silver hair, green eyes, elongated pointed elven ears (no horns), solo, {BODY_POS}",
        "negative": ", ".join([HORN_NEG, DUPLICATE_NEG, ANATOMY_NEG, BODY_NEG, TAIL_NEG]),
        "style": "masterpiece comic art, gritty noir sci-fi, bold ink, cel shading, dramatic rimlight, rain and neon, dynamic angle",
    },
}

def get_all_presets() -> Dict[str, Dict[str, str]]:
    return _DEFAULT_PRESETS

# ======================
# Construtor de Prompt
# ======================
def build_prompts(preset: Dict[str, str], nsfw_on: bool, framing: str, angle: str, pose_details: str, env_details: str) -> Tuple[str, str]:
    base_pos = preset.get("positive", "")
    base_neg = preset.get("negative", "")
    style = preset.get("style", "")

    final_pos = base_pos
    if "close-up" not in framing:
        final_pos += f", {TAIL_POS}"

    details_pos = f"{framing}, {angle}, {pose_details}"
    scene_desc = env_details

    if nsfw_on:
        final_neg = f"{base_neg}, {SENSUAL_NEG}"
        final_style = style
    else:
        final_neg = f"{base_neg}, {SFW_NEG}, {SENSUAL_POS}"
        final_style = "masterpiece comic art, dynamic action pose, confident stance, cinematic lighting"

    prompt = _fit_to_limit(f"{final_pos}, {details_pos}, style: {final_style}, Scene: {scene_desc}")
    negative_prompt = _fit_to_limit(final_neg)
    
    return prompt, negative_prompt

# ===================================================
# UI – Botão Principal
# ===================================================
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    title: str = "🎞️ Diretor de Arte (Nerith)",
    ui=None,
    key_prefix: str = "",
) -> None:
    ui = ui or st
    key_prefix = (key_prefix or "nerith_comics").replace(" ", "_")

    try:
        ui.markdown(f"### {title}")

        # --- Verificação de Disponibilidade do USO ---
        if not USO_AVAILABLE:
            ui.info(
                "O modelo 'USO' não está disponível. Para habilitá-lo, certifique-se de que o arquivo `uso_wrapper.py` "
                "e as dependências do modelo (como `inference.py`) estão no diretório do projeto e as bibliotecas instaladas."
            )

        c1, c2 = ui.columns(2)
        if not PROVIDERS:
            ui.error("Nenhum modelo de imagem está configurado. Verifique a instalação.")
            return
            
        prov_key = c1.selectbox("Modelo", options=list(PROVIDERS.keys()), index=0, key=f"{key_prefix}_model_sel")
        cfg = PROVIDERS.get(prov_key, {})
        provider_name = cfg.get("provider")
        model_name, size = cfg.get("model"), cfg.get("size")

        all_presets = get_all_presets()
        sel_preset = c2.selectbox("Preset de Estilo", options=list(all_presets.keys()), index=0, key=f"{key_prefix}_preset_sel")
        cur = dict(all_presets.get(sel_preset, {}))

        ui.markdown("---")
        ui.subheader("Direção da Cena")

        style_image_path = ""
        if provider_name == "uso_local":
            style_image_path = ui.text_input("Imagem de Estilo (em `assets/`)", placeholder="Ex: style.webp", key=f"{key_prefix}_style_img")

        col_framing, col_angle = ui.columns(2)
        framing_map = {"Retrato (close-up)": "close-up shot, portrait", "Meio corpo (medium shot)": "medium shot, cowboy shot, waist up", "Corpo inteiro (full body)": "full body shot, full length"}
        framing_choice = col_framing.selectbox("Enquadramento", options=list(framing_map.keys()), index=2, key=f"{key_prefix}_framing")
        framing = framing_map[framing_choice]

        angle_map = {"De frente": "front view, facing the viewer", "De lado": "side view, profile shot", "De costas": "from behind, back view", "Três quartos": "three-quarter view"}
        angle_choice = col_angle.selectbox("Ângulo", options=list(angle_map.keys()), index=3, key=f"{key_prefix}_angle")
        angle = angle_map[angle_choice]

        with ui.expander("Direção de Arte (Opcional)"):
            pose_details = st.text_input("Detalhes da Pose e Ação", placeholder="Ex: olhando por cima do ombro...", key=f"{key_prefix}_pose_details")
            env_details = st.text_input("Detalhes do Ambiente", placeholder="Ex: em uma varanda com vista para a cidade...", key=f"{key_prefix}_env_details")

        ui.markdown("---")
        nsfw_on = ui.toggle("Liberar sensualidade implícita", value=True, key=f"{key_prefix}_nsfw_toggle")
        gen = ui.button("Gerar Painel", use_container_width=True, key=f"{key_prefix}_gen_btn")

        if not gen:
            return

        prompt, negative_prompt = build_prompts(cur, nsfw_on, framing, angle, pose_details, env_details)

        with ui.expander("Ver prompts finais", expanded=False):
            st.markdown("**Prompt Positivo:**"); st.code(prompt, language=None)
            st.markdown("**Prompt Negativo:**"); st.code(negative_prompt, language=None)

        width, height = map(int, str(size).lower().split("x"))
        
        with st.spinner("Gerando painel com o modelo selecionado…"):
            if provider_name == "uso_local":
                # Validação de segurança para o caminho da imagem
                if not style_image_path:
                    st.error("Para o modelo USO, forneça o nome do arquivo da imagem de estilo.")
                    return
                
                # Garante que o caminho é seguro e aponta para o diretório 'assets'
                safe_base_dir = os.path.abspath("assets")
                full_path = os.path.abspath(os.path.join(safe_base_dir, style_image_path))
                
                if not full_path.startswith(safe_base_dir) or not os.path.exists(full_path):
                    st.error(f"Imagem de estilo não encontrada ou caminho inválido. Verifique se '{style_image_path}' existe dentro da pasta 'assets/'.")
                    return
                
                img = uso_wrapper.generate_image(prompt=prompt, negative_prompt=negative_prompt, style_image_path=full_path, width=width, height=height)
            else:
                client = _hf_client()
                img_data = client.text_to_image(model=model_name, prompt=prompt, negative_prompt=negative_prompt, width=width, height=height)
                img = Image.open(io.BytesIO(img_data)) if isinstance(img_data, bytes) else img_data

        ui.image(img, caption=f"Painel gerado com '{prov_key}'", use_column_width=True)
        buf = io.BytesIO(); buf.name = 'nerith_quadrinho.png'
        img.save(buf, "PNG")
        ui.download_button("⬇️ Baixar PNG", data=buf, file_name=buf.name, mime="image/png", key=f"{key_prefix}_dl_btn")

    except Exception as e:
        ui.error(f"Falha na geração de quadrinhos: {e}")
        st.exception(e)
