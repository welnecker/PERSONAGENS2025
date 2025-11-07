# ============================================================
# characters/nerith/comics.py — VERSÃO COM CHAMADA DIRETA PARA QWEN
# ============================================================
from __future__ import annotations
import os, io, re, json
from typing import Callable, List, Dict, Tuple, Optional
from PIL import Image
from huggingface_hub import InferenceClient
import streamlit as st
import requests # Importa a biblioteca requests

# ============================================================
# PROVIDERS, PRESETS, e outras constantes
# ============================================================

def parse_size(s: str) -> Tuple[int, int]:
    s = (s or "1024x1024").lower().replace("×", "x").strip()
    m = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", s)
    if not m: return (1024, 1024)
    return int(m.group(1)), int(m.group(2))

# ✅ Modelo reintroduzido na lista
PROVIDERS: Dict[str, Dict[str, object]] = {
    "FAL • Qwen Image Studio (Realism)": {"provider": "fal-ai", "model": "prithivMLmods/Qwen-Image-Studio-Realism", "size": "1024x1024", "sdxl": False, "qwen": True, "direct_call": True}, # Flag para chamada direta
    "FAL • Dark Fantasy Flux": {"provider": "fal-ai", "model": "nerijs/dark-fantasy-illustration-flux", "size": "1024x1024", "sdxl": False, "darkflux": True},
    "FAL • SDXL Lightning": {"provider": "fal-ai", "model": "ByteDance/SDXL-Lightning", "size": "1152x896", "sdxl": True, "lightning": True},
    "FAL • Stable Image Ultra": {"provider": "fal-ai", "model": "stabilityai/stable-image-ultra", "size": "1024x1024", "sdxl": False},
    "FAL • Qwen Image": {"provider": "fal-ai", "model": "Qwen/Qwen-Image", "size": "1024x1024", "sdxl": False, "qwen": True},
    "HF • FLUX.1-dev": {"provider": "huggingface", "model": "black-forest-labs/FLUX.1-dev", "size": "1024x1024", "sdxl": False},
    "HF • SDXL (nscale)": {"provider": "huggingface-nscale", "model": "stabilityai/stable-diffusion-xl-base-1.0", "size": "1152x896", "sdxl": True},
    "HF • SDXL (nscale + Refiner)": {"provider": "huggingface-nscale", "model": "stabilityai/stable-diffusion-xl-refiner-1.0", "size": "1152x896", "sdxl": True, "refiner": True},
}

# ... (O restante das constantes como SDXL_SIZES, PRESETS, etc., permanece o mesmo) ...
# Importando de presets.py
try:
    from .presets import *
except ImportError:
    # Fallback se o arquivo não existir, para manter a funcionalidade
    FACE_POS = "striking mediterranean features, almond-shaped captivating eyes, defined cheekbones, soft cat-eye eyeliner, full lips, mature confident allure, intense gaze"
    BODY_POS = "hourglass figure, soft athletic tone, firm natural breasts, narrow waist, defined abdomen, high-set rounded glutes"
    ANATOMY_NEG = "bad anatomy, deformed body, mutated body, malformed limbs, warped body, twisted spine, extra limbs, fused fingers, missing fingers"
    BODY_NEG = "balloon breasts, implants, sagging breasts, torpedo breasts, plastic body, barbie proportions, distorted waist"
    CELEB_NEG = "celebrity, celebrity lookalike, look alike, famous actress, face recognition match, portrait of a celebrity, sophia loren, monica bellucci, penelope cruz, gal gadot, angelina jolie"
    SENSUAL_POS = "subtle sensual posture, cinematic shadows caressing the skin, dramatic rimlight, implicit sensuality"
    SENSUAL_NEG = "explicit, pornographic, nude, censored, text, watermark"
    TAIL_POS = "a single biomechanical blade-tail fused to the spine, silver metal, sharp edges, blue glowing energy vein"
    TAIL_NEG = "furry tail, fleshy tail, animal tail, penis tail, detached tail"
    DOLL_NEG = "doll, barbie, plastic skin, CGI skin, beauty-filter, uncanny-valley, over-smooth skin, poreless skin, wax figure"
    INK_LINE_POS = "inked line art, strong outlines, cel shading, halftone dots, cross-hatching, textured paper grain, gritty shadows"
    COMIC_ADULT = "adult comic illustration, dark mature tone, dramatic chiaroscuro, rich blacks, heavy shadows, limited palette"
    DEFAULT_NEG = f"{ANATOMY_NEG}, {BODY_NEG}, {TAIL_NEG}, {DOLL_NEG}, {CELEB_NEG}, watermark, text, signature"
    IDENTITY_ANCHOR = "Nerith, original character, female dark-elf (drow) with blue-slate matte skin, long metallic silver hair, vivid emerald-green eyes, elongated pointed elven ears (no horns), solo subject, elegant yet fierce presence"
    PRESETS = {"Qwen • Nerith Realism Comic": {"positive": f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, comic-realism balance, defined pores, natural skin texture", "negative": DEFAULT_NEG, "style": "realistic comic portrait, cinematic shadows, strong outlines"}}


SDXL_SIZES: Dict[str, Tuple[int, int]] = {
    "1152×896 (horizontal)": (1152, 896), "896×1152 (vertical)": (896, 1152),
    "1216×832 (wide)": (1216, 832), "832×1216 (tall)": (832, 1216),
    "1024×1024": (1024, 1024),
}

def _get_hf_token() -> str:
    tok = (str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or str(st.secrets.get("HF_TOKEN", "")) or os.environ.get("HUGGINGFACE_API_KEY", "") or os.environ.get("HF_TOKEN", ""))
    if not tok.strip(): raise RuntimeError("⚠️ Defina HUGGINGFACE_API_KEY ou HF_TOKEN em st.secrets ou ambiente.")
    return tok.strip()

def get_client(provider: str) -> InferenceClient:
    pv = (provider or "").strip().lower()
    token = _get_hf_token()
    if pv in ("huggingface-nscale", "nscale", "hf-nscale"): return InferenceClient(provider="nscale", api_key=token)
    if pv in ("fal-ai", "fal", "falai"): return InferenceClient(provider="fal-ai", api_key=token)
    return InferenceClient(token=token)

def build_prompts(preset: Dict[str, str], nsfw_on: bool, framing: str, angle: str, pose: str, env: str) -> Tuple[str, str]:
    base_pos, base_neg, style = preset.get("positive", ""), preset.get("negative", ""), preset.get("style", "")
    final_pos = base_pos
    angle_str = (angle or "").lower()
    if "back view" in angle_str:
        final_pos = final_pos.replace(FACE_POS, "").replace(FACE_SIG, "")
        angle_details = "(back view:1.2), (from behind), showing her back, back of the head"
    elif "side view" in angle_str:
        final_pos = final_pos.replace(FACE_POS, "face in profile").replace(FACE_SIG, "face in profile")
        angle_details = "(side view:1.1), profile view"
    else: angle_details = angle
    if "close-up" not in (framing or ""):
        if "back view" in angle_str: final_pos += f", (focus on {TAIL_POS}:1.2)"
        else: final_pos += f", {TAIL_POS}"
    final_pos += f", {framing}, {angle_details}"
    if (pose or "").strip(): final_pos += f", {pose.strip()}"
    if (env or "").strip(): final_pos += f", scene: {env.strip()}"
    if nsfw_on: final_style, final_neg = style, f"{base_neg}, {SENSUAL_NEG}"
    else: final_style, final_neg = "cinematic, elegant, dramatic lighting", f"{base_neg}, {SENSUAL_POS}"
    prompt = " ".join(f"{final_pos}, style: {final_style}".split())[:1800]
    negative = " ".join(final_neg.split())[:1800]
    return prompt, negative

def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    title: str = "🎞️ Quadrinho (beta)",
    ui=None,
    key_prefix: str = "",
    providers_dict: Dict[str, Dict[str, object]] = PROVIDERS,
    presets_dict: Dict[str, Dict[str, str]] = PRESETS,
    sdxl_sizes_dict: Dict[str, Tuple[int, int]] = SDXL_SIZES,
) -> None:
    ui = ui or st
    key_prefix = (key_prefix or "nerith_comics").replace(" ", "_")
    try:
        ui.markdown(f"### {title}")
        model_keys = list(providers_dict.keys())
        c1, c2 = ui.columns(2)
        prov_key = c1.selectbox("Modelo", model_keys, index=0, key=f"{key_prefix}_model")
        cfg = providers_dict.get(prov_key, {})
        
        preset_list = list(presets_dict.keys())
        # Auto-seleciona o preset
        if prov_key == "FAL • Dark Fantasy Flux": default_preset = "FLUX • Nerith Dark Fantasy"
        elif "Qwen" in prov_key: default_preset = "Qwen • Nerith Realism Comic"
        elif cfg.get("sdxl"): default_preset = "SDXL • Nerith Comic (Adulto)"
        else: default_preset = "FLUX • Nerith HQ"
        default_idx = preset_list.index(default_preset) if default_preset in preset_list else 0
        preset_name = c2.selectbox("Preset", preset_list, index=default_idx, key=f"{key_prefix}_preset")
        preset = presets_dict[preset_name]

        # ... (Restante da UI permanece igual)
        ui.markdown("---")
        ui.subheader("Direção da Cena")
        col_f, col_a = ui.columns(2)
        framing_map = {"Retrato (close-up)": "close-up portrait", "Meio corpo": "medium shot", "Corpo inteiro": "full body"}
        framing = framing_map[col_f.selectbox("Enquadramento", list(framing_map.keys()), index=2, key=f"{key_prefix}_frame")]
        angle_map = {"Frente": "front view", "Lado": "side view", "Costas": "back view", "Três quartos": "three-quarter view"}
        angle = angle_map[col_a.selectbox("Ângulo", list(angle_map.keys()), index=3, key=f"{key_prefix}_angle")]
        with ui.expander("Direção de Arte (Opcional)"):
            pose = ui.text_input("Pose / Ação", key=f"{key_prefix}_pose")
            env = ui.text_input("Ambiente / Cenário", key=f"{key_prefix}_env")
        ui.markdown("---")
        nsfw = ui.toggle("Liberar sensualidade implícita", value=True, key=f"{key_prefix}_nsfw")
        mad = ui.toggle("🔥 Modo Automático Anti-Deformações", value=True, key=f"{key_prefix}_mad")
        if cfg.get("sdxl"):
            sz = ui.selectbox("📐 Resolução SDXL", list(sdxl_sizes_dict.keys()), index=0, key=f"{key_prefix}_size")
            width, height = sdxl_sizes_dict[sz]
        else:
            width, height = parse_size(str(cfg.get("size", "1024x1024")))
        col_s, col_g = ui.columns(2)
        if cfg.get("lightning"): steps, guidance = col_s.slider("Steps", 4, 24, 8, key=f"{key_prefix}_steps"), col_g.slider("Guidance", 0.0, 2.0, 1.5, key=f"{key_prefix}_guidance")
        elif cfg.get("sdxl"): steps, guidance = col_s.slider("Steps", 20, 60, 32, key=f"{key_prefix}_steps"), col_g.slider("Guidance", 2.0, 12.0, 7.0, key=f"{key_prefix}_guidance")
        else: steps, guidance = col_s.slider("Steps", 20, 60, 30, key=f"{key_prefix}_steps"), col_g.slider("Guidance", 2.0, 12.0, 7.0, key=f"{key_prefix}_guidance")
        if cfg.get("qwen"): steps, guidance = min(steps, 24), min(guidance, 6.0)
        if mad:
            if cfg.get("lightning"): guidance, steps = min(1.8, guidance), max(6, min(12, steps))
            elif prov_key == "FAL • Dark Fantasy Flux": guidance, steps = 6.3, max(30, steps)
            elif cfg.get("sdxl"): guidance, steps = 5.6, max(32, steps)
            elif cfg.get("qwen"): guidance, steps = min(5.5, guidance), max(18, steps)
            else: guidance, steps = 7.2, max(26, steps)

        if not ui.button("Gerar Painel 🎨", use_container_width=True, key=f"{key_prefix}_go"): return

        prompt, negative = build_prompts(preset, nsfw, framing, angle, pose, env)
        if mad: negative += ", " + ", ".join(["barbie-doll", "plastic texture", "CGI texture", "over-smooth shader", "beauty-filtered skin", "poreless skin", "plastic face", "barbie face"])
        
        with ui.expander("Prompts finais"): ui.code(prompt); ui.code(negative)
        ui.info(f"✅ Provider: {cfg.get('provider')} — Modelo: {cfg.get('model')} ({width}×{height}, steps={steps}, guidance={guidance})")

        # ✅ INÍCIO DA CORREÇÃO: Lógica de chamada condicional
        if cfg.get("direct_call"):
            with st.spinner("Gerando painel com chamada direta..."):
                # Endpoint da API para o modelo fal-ai
                url = f"https://fal.run/{str(cfg.get('model' )).replace('/', '-')}"
                
                # Payload JSON montado manualmente, sem o campo 'loras'
                payload = {
                    "prompt": prompt,
                    "negative_prompt": negative,
                    "width": width,
                    "height": height,
                    "num_inference_steps": steps,
                    "guidance_scale": guidance,
                }
                
                headers = {
                    "Authorization": f"Key {_get_hf_token()}",
                    "Content-Type": "application/json"
                }
                
                response = requests.post(url, json=payload, headers=headers)
                response.raise_for_status() # Lança um erro se a resposta não for 2xx
                
                # A API do fal.run retorna um JSON com a URL da imagem
                result = response.json()
                image_url = result['images'][0]['url']
                
                # Baixa a imagem da URL
                image_response = requests.get(image_url)
                image_response.raise_for_status()
                img_data = image_response.content

        elif cfg.get("refiner"):
            client = get_client(str(cfg.get("provider")))
            with st.spinner("Etapa 1: SDXL Base..."): base_img = client.text_to_image(prompt=prompt, model="stabilityai/stable-diffusion-xl-base-1.0", negative_prompt=negative, width=width, height=height, num_inference_steps=steps, guidance_scale=guidance)
            with st.spinner("Etapa 2: Refiner..."): img_data = client.text_to_image(prompt=prompt, model="stabilityai/stable-diffusion-xl-refiner-1.0", negative_prompt=negative, image=base_img, num_inference_steps=steps, guidance_scale=guidance)
        else:
            client = get_client(str(cfg.get("provider")))
            if cfg.get("lightning"): guidance = min(guidance, 2.0)
            params = {"prompt": prompt, "model": str(cfg.get("model")), "negative_prompt": negative, "width": width, "height": height, "num_inference_steps": steps, "guidance_scale": guidance}
            with st.spinner("Gerando painel..."): img_data = client.text_to_image(**params)
        # ✅ FIM DA CORREÇÃO

        img = Image.open(io.BytesIO(img_data))
        ui.image(img, caption=f"Preset: {preset_name}", use_column_width=True)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        ui.download_button("⬇️ Baixar PNG", data=buf.getvalue(), file_name="nerith_comic.png", mime="image/png", key=f"{key_prefix}_dl")

    except Exception as e:
        ui.error(f"Erro: {e}")
        try:
            import traceback
            ui.code("".join(traceback.format_exc()))
        except Exception: pass
