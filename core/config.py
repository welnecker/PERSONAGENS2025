# core/config.py
from __future__ import annotations

import os
from dataclasses import dataclass

# Tenta ler st.secrets quando rodando no Streamlit
try:
    import streamlit as st
    _SECRETS = getattr(st, "secrets", {})
except Exception:
    _SECRETS = {}


def _pick(*names: str, default: str = "") -> str:
    """
    Retorna o primeiro valor disponível entre:
      1) st.secrets[name]
      2) os.environ[name]
      3) default
    Aceita múltiplos nomes (útil para compat: OPENROUTER_API_KEY | OPENROUTER_TOKEN).
    """
    for name in names:
        val = None
        try:
            if hasattr(_SECRETS, "get"):
                val = _SECRETS.get(name)
        except Exception:
            val = None
        if val is None:
            val = os.getenv(name)
        if val not in (None, ""):
            return str(val)
    return str(default)


@dataclass
class _Settings:
    # App
    APP_NAME: str = _pick("APP_NAME", default="personagens2025")
    APP_PUBLIC_URL: str = _pick("APP_PUBLIC_URL", default="")

    # DB backend (memory|mongo)
    DB_BACKEND: str = _pick("DB_BACKEND", default="memory")

    # Mongo (opcional)
    MONGO_USER: str = _pick("MONGO_USER", default="")
    MONGO_PASS: str = _pick("MONGO_PASS", default="")
    MONGO_CLUSTER: str = _pick("MONGO_CLUSTER", default="")

    # LLM timeout
    LLM_HTTP_TIMEOUT: str = _pick("LLM_HTTP_TIMEOUT", default="60")

    # OpenRouter (aceita TOKEN antigo como fallback)
    OPENROUTER_API_KEY: str = _pick("OPENROUTER_API_KEY", "OPENROUTER_TOKEN", default="")
    OPENROUTER_BASE_URL: str = _pick(
        "OPENROUTER_BASE_URL",
        default="https://openrouter.ai/api/v1/chat/completions",
    )

    # Together
    TOGETHER_API_KEY: str = _pick("TOGETHER_API_KEY", default="")
    TOGETHER_BASE_URL: str = _pick(
        "TOGETHER_BASE_URL",
        default="https://api.together.xyz/v1/chat/completions",
    )

    def ensure_env(self) -> None:
        """
        Propaga valores para os.environ para que módulos que acessam direto
        via os.getenv(...) encontrem as chaves corretamente.
        """
        os.environ.setdefault("APP_NAME", self.APP_NAME)
        os.environ.setdefault("LLM_HTTP_TIMEOUT", str(self.LLM_HTTP_TIMEOUT))

        # Backend do BD
        os.environ.setdefault("DB_BACKEND", self.DB_BACKEND)
        if self.MONGO_USER:
            os.environ.setdefault("MONGO_USER", self.MONGO_USER)
        if self.MONGO_PASS:
            os.environ.setdefault("MONGO_PASS", self.MONGO_PASS)
        if self.MONGO_CLUSTER:
            os.environ.setdefault("MONGO_CLUSTER", self.MONGO_CLUSTER)

        # OpenRouter
        if self.OPENROUTER_API_KEY:
            os.environ["OPENROUTER_API_KEY"] = self.OPENROUTER_API_KEY
        os.environ.setdefault("OPENROUTER_BASE_URL", self.OPENROUTER_BASE_URL)

        # Together
        if self.TOGETHER_API_KEY:
            os.environ["TOGETHER_API_KEY"] = self.TOGETHER_API_KEY
        os.environ.setdefault("TOGETHER_BASE_URL", self.TOGETHER_BASE_URL)

    def mongo_uri(self) -> str:
        """
        Monta a URI do MongoDB se houver credenciais (senão retorna string vazia).
        """
        if not (self.MONGO_USER and self.MONGO_PASS and self.MONGO_CLUSTER):
            return ""
        from urllib.parse import quote_plus
        return (
            f"mongodb+srv://{self.MONGO_USER}:{quote_plus(self.MONGO_PASS)}"
            f"@{self.MONGO_CLUSTER}/?retryWrites=true&w=majority&appName={self.APP_NAME}"
        )


# Instância única e propagação p/ ambiente
settings = _Settings()
settings.ensure_env()
