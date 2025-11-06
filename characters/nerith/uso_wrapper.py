# characters/nerith/uso_wrapper.py
import os
import torch
import streamlit as st
from PIL import Image, ImageDraw
from pathlib import Path
from huggingface_hub import hf_hub_download

# Este import agora pode falhar, e o comics.py irá tratar a ausência do wrapper.
from inference import setup_model, generate

# --- Variáveis Globais ---
_device = "cuda" if torch.cuda.is_available() else "cpu"

@st.cache_resource
def download_and_get_paths() -> dict:
    """
    Baixa os checkpoints do USO do Hugging Face Hub e retorna os caminhos.
    Usa o cache do Streamlit para baixar apenas uma vez.
    """
    print("USO Wrapper: Baixando checkpoints (executado apenas uma vez)...")
    save_dir = Path("./uso_checkpoints")
    save_dir.mkdir(exist_ok=True)
    
    lora_path = hf_hub_download(
        repo_id="bytedance-research/USO",
        filename="uso_flux_v1.0/dit_lora.safetensors",
        local_dir=save_dir,
        local_dir_use_symlinks=False
    )
    projector_path = hf_hub_download(
        repo_id="bytedance-research/USO",
        filename="uso_flux_v1.0/projector.safetensors",
        local_dir=save_dir,
        local_dir_use_symlinks=False
    )
    
    print("USO Wrapper: Checkpoints baixados.")
    return {
        "flux_path": "black-forest-labs/FLUX.1-dev",
        "t5_path": "google-t5/t5-v1_1-xxl",
        "clip_path": "openai/clip-vit-large-patch14",
        "lora_path": str(lora_path),
        "projector_path": str(projector_path),
    }

@st.cache_resource
def get_uso_models() -> dict:
    """
    Carrega e cacheia os modelos na memória usando o cache de recursos do Streamlit.
    Isso garante que os modelos sejam carregados apenas uma vez durante a sessão do app.
    """
    print("USO Wrapper: Carregando modelos na memória (executado apenas uma vez)...")
    paths = download_and_get_paths()
    
    os.environ.setdefault("FLUX_DEV", paths["flux_path"])
    os.environ.setdefault("T5", paths["t5_path"])
    os.environ.setdefault("CLIP", paths["clip_path"])
    os.environ.setdefault("LORA", paths["lora_path"])
    os.environ.setdefault("PROJECTION_MODEL", paths["projector_path"])

    models = setup_model(_device)
    print("USO Wrapper: Modelos carregados e cacheados com sucesso.")
    return models

def generate_image(
    prompt: str,
    negative_prompt: str,
    style_image_path: str,
    width: int = 1024,
    height: int = 1024,
    num_inference_steps: int = 30,
    style_strength: float = 0.8,
) -> Image.Image:
    """
    Função principal que gera uma imagem usando o modelo USO.
    """
    try:
        # 1. Obter modelos (usando cache do Streamlit)
        models = get_uso_models()

        # 2. Preparar os caminhos das imagens de referência
        image_paths = ["", style_image_path]

        # 3. Chamar a função de geração do script original do USO
        generated_images = generate(
            models=models,
            prompt=prompt,
            neg_prompt=negative_prompt,
            image_paths=image_paths,
            width=width,
            height=height,
            num_inference_steps=num_inference_steps,
            style_strength=style_strength,
            device=_device,
        )

        if not generated_images:
            raise RuntimeError("A geração com USO não retornou nenhuma imagem.")

        return generated_images[0]

    except Exception as e:
        print(f"Erro no wrapper do USO: {e}")
        # Retorna uma imagem de erro para feedback visual no app
        error_img = Image.new('RGB', (512, 512), color='red')
        d = ImageDraw.Draw(error_img)
        d.text((10, 10), f"USO Error:\n{e}", fill=(255, 255, 255))
        return error_img
