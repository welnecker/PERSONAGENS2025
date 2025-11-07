# characters/nerith/uso_wrapper.py
import os
import torch
import streamlit as st
from PIL import Image, ImageDraw
from pathlib import Path

# --- Importações essenciais do código do USO ---
# [IMPORTANTE] Note como o import agora começa com "uso."
# Isso funciona porque o requirements.txt instalou o pacote "uso".
try:
    from uso.flux.pipeline import USOPipeline, preprocess_ref
    from transformers import SiglipVisionModel, SiglipImageProcessor
except ImportError as e:
    raise ImportError(
        "Não foi possível importar os componentes do USO. "
        "Certifique-se de que o pacote está instalado corretamente (via 'pip install git+https://github.com/bytedance-research/USO.git' ) "
        f"e as dependências estão no requirements.txt. Detalhe: {e}"
    )

# --- Variáveis Globais e Cache ---
_device = "cuda" if torch.cuda.is_available() else "cpu"

@st.cache_resource
def get_siglip_model():
    """Carrega e cacheia o modelo SigLIP."""
    try:
        # O caminho pode ser um repo do HF ou um caminho local
        siglip_path = os.getenv("SIGLIP_PATH", "google/siglip-so400m-patch14-384")
        print(f"USO Wrapper: Carregando SigLIP de '{siglip_path}'...")
        processor = SiglipImageProcessor.from_pretrained(siglip_path)
        model = SiglipVisionModel.from_pretrained(siglip_path)
        model.eval().to(_device)
        print("USO Wrapper: Modelo SigLIP carregado com sucesso.")
        return model, processor
    except Exception as e:
        st.error(f"Falha ao carregar o modelo SigLIP: {e}")
        print(f"Erro detalhado ao carregar SigLIP: {e}")
        return None, None

@st.cache_resource
def get_uso_pipeline():
    """Carrega e cacheia o pipeline principal do USO."""
    print("USO Wrapper: Carregando pipeline USO pela primeira vez...")
    siglip_model, _ = get_siglip_model()
    
    # hf_download=False é crucial para garantir que ele use os caminhos locais
    # que o diffusers encontra em seu cache ou que são definidos por variáveis de ambiente.
    pipeline = USOPipeline(
        model_type="flux-dev",
        device=_device,
        offload=False,
        only_lora=True,
        lora_rank=128,
        hf_download=False, # MUITO IMPORTANTE: Evita downloads automáticos indesejados.
    )
    
    if siglip_model:
        pipeline.model.vision_encoder = siglip_model
        
    print("USO Wrapper: Pipeline USO carregado e cacheado com sucesso.")
    return pipeline

def generate_image(
    prompt: str,
    negative_prompt: str, # Embora o pipeline original não use, mantemos por consistência
    style_image_path: str,
    width: int = 1024,
    height: int = 1024,
    num_inference_steps: int = 25,
    guidance: float = 4.0,
    seed: int = 3407,
) -> Image.Image:
    """
    Função adaptada para gerar uma imagem usando o pipeline do USO.
    """
    try:
        # 1. Obter o pipeline e os processadores cacheados
        pipeline = get_uso_pipeline()
        _, siglip_processor = get_siglip_model()

        if not pipeline or not siglip_processor:
            raise RuntimeError("Pipeline USO ou processador SigLIP não puderam ser inicializados.")

        # 2. Carregar e processar a imagem de estilo
        style_image = Image.open(style_image_path).convert("RGB")
        
        # O pipeline do USO espera uma lista de imagens de referência.
        # A primeira é para conteúdo (vazia no nosso caso) e a segunda para estilo.
        ref_imgs = [None, style_image]

        # 3. Preparar inputs para o SigLIP (processador de imagem de estilo)
        with torch.no_grad():
            siglip_inputs = [
                siglip_processor(img, return_tensors="pt").to(_device)
                for img in ref_imgs[1:] if isinstance(img, Image.Image)
            ]

        # 4. Chamar o pipeline com os argumentos corretos
        print("USO Wrapper: Gerando imagem...")
        generated_image = pipeline(
            prompt=prompt,
            width=width,
            height=height,
            guidance=guidance,
            num_steps=num_inference_steps,
            seed=seed,
            ref_imgs=[], # A referência de conteúdo não é usada para transferência de estilo
            pe="d", # Valor padrão do script original
            siglip_inputs=siglip_inputs,
        )
        print("USO Wrapper: Imagem gerada com sucesso.")

        return generated_image

    except Exception as e:
        st.error(f"Erro durante a geração com USO: {e}")
        print(f"Erro detalhado no wrapper do USO: {e}")
        # Retorna uma imagem de erro para feedback visual
        error_img = Image.new('RGB', (512, 512), color='darkred')
        d = ImageDraw.Draw(error_img)
        d.text((10, 10), f"USO Wrapper Error:\n{e}", fill=(255, 255, 255))
        return error_img

