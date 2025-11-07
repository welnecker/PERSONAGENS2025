# characters/registry.py
from __future__ import annotations
from importlib import import_module
from typing import Dict, Tuple, Type, List, Optional, Any, Callable, Iterable
import inspect

from core.common.base_service import BaseCharacter

# Catálogo: nome visível -> (módulo, classe)
_CATALOG: Dict[str, Tuple[str, str]] = {
    "Mary":   ("characters.mary.service", "MaryService"),
    "Laura":  ("characters.laura.service", "LauraService"),
    "Nerith": ("characters.nerith.service", "NerithService"),
}

# Cache de serviços para não recriar instâncias a cada chamada
_SERVICE_CACHE: Dict[str, BaseCharacter] = {}

def list_characters() -> List[str]:
    return list(_CATALOG.keys())

# ==== utilidades para modelos por personagem ====

def _coerce_models(x: Any) -> List[str]:
    # Aceita list/tuple/set e converte em lista de strings únicas, preservando ordem
    seen = set()
    out: List[str] = []
    if x is None:
        return out
    if isinstance(x, (list, tuple, set)):
        iterable: Iterable = x
    else:
        # algo inesperado -> ignora
        return out
    for it in iterable:
        s = str(it).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out

def _load_subregistry_models(pkg_path: str) -> List[str]:
    """
    Tenta carregar modelos extras do submódulo registry de cada personagem.
    Ex.: para 'characters.nerith.service', procura 'characters.nerith.registry'
    e tenta ler:
      - função list_models() -> Iterable[str]
      - ou constante MODELS -> Iterable[str]
    """
    try:
        pkg = pkg_path.rsplit(".", 1)[0]  # ex.: 'characters.nerith'
        sub = import_module(f"{pkg}.registry")
    except Exception:
        return []

    # Prioridade: função list_models() > constante MODELS
    try:
        fn = getattr(sub, "list_models", None)
        if callable(fn):
            return _coerce_models(fn())
    except Exception:
        pass

    try:
        models = getattr(sub, "MODELS", None)
        return _coerce_models(models)
    except Exception:
        return []

def _merge_available_models(impl: Any, module_name: str) -> List[str]:
    """
    Mescla modelos do service (se houver) com os do sub-registry opcional.
    Remove duplicatas preservando a ordem de aparição.
    """
    merged: List[str] = []
    seen = set()

    # 1) modelos vindos do próprio service
    fn: Optional[Callable] = getattr(impl, "available_models", None)
    if callable(fn):
        try:
            svc_models = _coerce_models(fn())
            for m in svc_models:
                if m not in seen:
                    seen.add(m)
                    merged.append(m)
        except Exception:
            pass

    # 2) modelos extras do sub-registry (ex.: characters.nerith.registry)
    extra = _load_subregistry_models(module_name)
    for m in extra:
        if m not in seen:
            seen.add(m)
            merged.append(m)

    return merged

# ==== Adapter que mantém comportamento anterior e adiciona robustez ====

class _ServiceAdapter(BaseCharacter):
    """Adapta objetos sem interface completa, preenchendo métodos faltantes."""
    def __init__(self, impl: Any, module_name: str, fallback_title: str = "Personagem"):
        self._impl = impl
        self._module_name = module_name
        self.title = getattr(impl, "title", fallback_title)

    def render_sidebar(self, sb) -> None:
        fn: Optional[Callable] = getattr(self._impl, "render_sidebar", None)
        if callable(fn):
            try:
                fn(sb)
                return
            except Exception:
                pass
        # default
        super().render_sidebar(sb)

    def available_models(self) -> List[str]:
        models = _merge_available_models(self._impl, self._module_name)
        if models:
            return models
        return super().available_models()

    def reply(self, user: str, model: str) -> str:
        # 1) se a implementação tiver reply() útil, use-a
        fn: Optional[Callable] = getattr(self._impl, "reply", None)
        if callable(fn):
            try:
                # Evita recair no NotImplemented da BaseCharacter
                if not (inspect.ismethod(fn) and getattr(fn, "__func__", None) is BaseCharacter.reply):
                    return fn(user=user, model=model)
            except Exception:
                # se a implementação própria falhar, cai para o core
                pass
        # 2) fallback genérico: usa core.service.gerar_resposta
        from core.service import gerar_resposta
        character_name = (getattr(self._impl, "title", None) or self.title or "Mary").strip()
        return gerar_resposta(usuario="GLOBAL", prompt_usuario=user, model=model, character=character_name)

# ==== API principal ====

def get_service(name: Optional[str]) -> BaseCharacter:
    key = (name or "").strip().title()
    if key not in _CATALOG:
        key = "Mary"

    # cache
    if key in _SERVICE_CACHE:
        return _SERVICE_CACHE[key]

    module_name, cls_name = _CATALOG[key]

    # tenta importar a classe solicitada; em caso de erro, cai para Mary
    try:
        mod = import_module(module_name)
        cls: Type[Any] = getattr(mod, cls_name)
    except Exception:
        if key != "Mary":
            mod = import_module(_CATALOG["Mary"][0])
            cls = getattr(mod, _CATALOG["Mary"][1])
            key = "Mary"
        else:
            raise  # se até Mary falhar, deixe a exceção subir

    impl = cls()

    # Se já é BaseCharacter e override de reply() existe, retorna direto
    if isinstance(impl, BaseCharacter):
        impl_reply = getattr(impl, "reply", None)
        if callable(impl_reply) and not (
            inspect.ismethod(impl_reply) and getattr(impl_reply, "__func__", None) is BaseCharacter.reply
        ):
            _SERVICE_CACHE[key] = impl
            return impl

    # Em qualquer outra situação, adaptamos.
    wrapped = _ServiceAdapter(impl, module_name=module_name, fallback_title=key)
    _SERVICE_CACHE[key] = wrapped
    return wrapped

# Conveniência opcional para UI: listar modelos da personagem sem instanciar tela
def list_models_for_character(name: Optional[str]) -> List[str]:
    key = (name or "").strip().title()
    if key not in _CATALOG:
        key = "Mary"
    module_name, _ = _CATALOG[key]
    try:
        # instancia o serviço (vem do cache nas chamadas seguintes)
        svc = get_service(key)
        # se o adapter já mescla, reaproveita a lógica
        return _coerce_models(svc.available_models())
    except Exception:
        # fallback só com sub-registry (se existir)
        return _load_subregistry_models(module_name)
