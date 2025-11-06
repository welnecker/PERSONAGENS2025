# characters/nerith/uso_wrapper.py
import os
import torch
from PIL import Image
from pathlib import Path
from huggingface_hub import hf_hub_download

# Importa as funções e classes do código original do USO
# (Supondo que o inference.py e arquivos relacionados estejam no mesmo diretório ou no PYTHONPATH)
# Se o código do USO estiver em uma subpasta, ajuste o import.
# Ex: from uso_code.inference import setup_model, generate
try:
    from inference import setup_model, generate
except ImportError:
    raise ImportError(
        "Certifique-se de que o arquivo 'inference.py' do modelo USO e suas dependências "
        "estejam no mesmo diretório ou acessíveis via PYTHONPATH."
    )

# --- Variáveis Globais para Cachear os Modelos ---
# Isso evita recarregar os modelos a cada geração, o que seria muito lento.
_cached_models = None
_device = "cuda" if torch.cuda.is_available() else "cpu"

def _get_cached_models(flux_path, t5_path, clip_path, lora_path, projector_path):
    """
    Carrega e cacheia os modelos na memória para evitar recarregamentos.
    """
    global _cached_models
    if _cached_models is None:
        print("USO Wrapper: Carregando modelos pela primeira vez...")
        
        # Configura as variáveis de ambiente se não estiverem definidas
        os.environ.setdefault("FLUX_DEV", flux_path)
        os.environ.setdefault("T5", t5_path)
        os.environ.setdefault("CLIP", clip_path)
        os.environ.setdefault("LORA", lora_path)
        os.environ.setdefault("PROJECTION_MODEL", projector_path)

        # Chama a função de setup do script original do USO
        _cached_models = setup_model(_device)
        print("USO Wrapper: Modelos carregados e cacheados com sucesso.")
    return _cached_models

def download_and_get_paths() -> dict:
    """
    Baixa os checkpoints do USO do Hugging Face Hub e retorna os caminhos.
    """
    save_dir = Path("./uso_checkpoints")
    save_dir.mkdir(exist_ok=True)
    
    # Baixa os arquivos principais do USO
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
    
    # Os modelos base (FLUX, T5, CLIP) serão baixados automaticamente pelo diffusers
    # se não estiverem em cache. Apenas retornamos os nomes dos repositórios.
    return {
        "flux_path": "black-forest-labs/FLUX.1-dev",
        "t5_path": "google-t5/t5-v1_1-xxl",
        "clip_path": "openai/clip-vit-large-patch14",
        "lora_path": str(lora_path),
        "projector_path": str(projector_path),
    }

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
    
    Args:
        prompt (str): O prompt de texto.
        negative_prompt (str): O prompt negativo.
        style_image_path (str): Caminho para a imagem de referência de estilo.
        width (int): Largura da imagem.
        height (int): Altura da imagem.
        num_inference_steps (int): Passos de inferência.
        style_strength (float): Força da estilização.

    Returns:
        Image.Image: A imagem gerada.
    """
    try:
        # 1. Baixar checkpoints e obter caminhos
        paths = download_and_get_paths()

        # 2. Carregar modelos (usando cache)
        models = _get_cached_models(
            flux_path=paths["flux_path"],
            t5_path=paths["t5_path"],
            clip_path=paths["clip_path"],
            lora_path=paths["lora_path"],
            projector_path=paths["projector_path"],
        )

        # 3. Preparar os caminhos das imagens de referência
        # Para geração orientada a estilo, o caminho do conteúdo é vazio.
        image_paths = ["", style_image_path]

        # 4. Chamar a função de geração do script original do USO
        # A função 'generate' do USO pode precisar ser adaptada para retornar a imagem
        # em vez de salvá-la em disco. Se ela salvar, precisamos carregar a imagem salva.
        # Supondo que 'generate' retorna uma lista de imagens PIL.
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

        # Retorna a primeira imagem gerada
        return generated_images[0]

    except Exception as e:
        print(f"Erro no wrapper do USO: {e}")
        # Retorna uma imagem de erro para feedback visual no app
        error_img = Image.new('RGB', (512, 512), color = 'red')
        from PIL import ImageDraw
        d = ImageDraw.Draw(error_img)
        d.text((10,10), f"USO Error:\n{e}", fill=(255,255,255))
        return error_img

