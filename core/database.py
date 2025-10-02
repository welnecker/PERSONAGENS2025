# core/database.py
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from threading import RLock
import uuid
import os

from .config import settings

# --------- Tentativa de usar PyMongo (opcional) ---------
try:
    from pymongo import MongoClient
    HAVE_PYMONGO = True
except Exception:
    HAVE_PYMONGO = False

_CLIENT: Optional["MongoClient"] = None
_BACKEND: Optional[str] = None
_REASON: str = ""


# ===================== BACKEND: MEMÓRIA =====================
_STORE: Dict[str, List[Dict[str, Any]]] = {}
_LOCK = RLock()


def _include_projection(doc: Dict[str, Any], proj: Optional[Dict[str, int]]) -> Dict[str, Any]:
    """Suporte simples a projeção de inclusão (valores 1)."""
    if not proj:
        return dict(doc)
    include_keys = [k for k, v in proj.items() if v]
    if not include_keys:
        return dict(doc)
    out = {"_id": doc.get("_id")}
    for k in include_keys:
        # suporte raso (sem dots) para uso típico do app
        if "." in k:
            # projeções com dot: melhor retornar doc inteiro do que falhar
            return dict(doc)
        if k in doc:
            out[k] = doc[k]
    return out


class MemoryCollection:
    """Coleção em memória com API similar ao PyMongo Collection."""
    def __init__(self, name: str):
        self.name = name
        _STORE.setdefault(name, [])

    def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            d = dict(doc)
            d.setdefault("_id", str(uuid.uuid4()))
            _STORE[self.name].append(d)
            return {"inserted_id": d["_id"]}

    def _match(self, doc: Dict[str, Any], filt: Optional[Dict[str, Any]]) -> bool:
        if not filt:
            return True
        for k, v in filt.items():
            if isinstance(v, dict) and "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def find(
        self,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        with _LOCK:
            rows = [d.copy() for d in _STORE.get(self.name, []) if self._match(d, filter)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda x: x.get(key), reverse=(direction < 0))
        if limit:
            rows = rows[:limit]
        if projection:
            rows = [_include_projection(d, projection) for d in rows]
        return rows

    def find_one(
        self,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = list(self.find(filter=filter, projection=projection, sort=sort, limit=1))
        return rows[0] if rows else None

    def update_one(self, filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            for d in rows:
                if self._match(d, filter):
                    if "$set" in update and isinstance(update["$set"], dict):
                        d.update(update["$set"])
                    else:
                        d.update(update)
                    return
            if upsert:
                doc = dict(filter)
                if "$set" in update and isinstance(update["$set"], dict):
                    doc.update(update["$set"])
                self.insert_one(doc)

    def delete_many(self, filter: Dict[str, Any]) -> int:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            before = len(rows)
            rows[:] = [d for d in rows if not self._match(d, filter)]
            return before - len(rows)


# ===================== SELEÇÃO DO BACKEND =====================
def _try_connect_mongo() -> bool:
    global _CLIENT, _REASON
    if not HAVE_PYMONGO:
        _REASON = "pymongo não instalado"
        return False
    uri = settings.mongo_uri()
    if not uri:
        _REASON = "mongo_uri vazio (falta DB_BACKEND=mongo ou credenciais)"
        return False
    try:
        timeout = float(os.getenv("LLM_HTTP_TIMEOUT", settings.LLM_HTTP_TIMEOUT or "60"))
    except Exception:
        timeout = 60.0
    try:
        _CLIENT = MongoClient(uri, serverSelectionTimeoutMS=int(timeout * 1000))
        # Sanity ping
        _CLIENT.admin.command("ping")
        _REASON = "conectado"
        return True
    except Exception as e:
        _CLIENT = None
        _REASON = f"falha de conexão: {e}"
        return False


def _choose_backend() -> str:
    global _BACKEND
    if _BACKEND:
        return _BACKEND
    if (settings.DB_BACKEND or "").lower() == "mongo":
        if _try_connect_mongo():
            _BACKEND = "mongo"
            return _BACKEND
        # se não conectar, cai para memória
    _BACKEND = "memory"
    return _BACKEND


# ===================== API PÚBLICA =====================
def get_col(name: str):
    """
    Retorna Collection (PyMongo) ou MemoryCollection (fallback).
    A API usada no projeto (insert_one, find, find_one, update_one, delete_many)
    está coberta em ambos.
    """
    if _choose_backend() == "mongo" and _CLIENT is not None:
        db = _CLIENT.get_database(settings.APP_NAME)
        return db.get_collection(name)
    return MemoryCollection(name)


def db_status() -> Tuple[str, str]:
    """
    Retorna (backend, detalhe). Útil para mostrar no sidebar.
    """
    backend = _choose_backend()
    detail = "Mongo — " + _REASON if backend == "mongo" else "Memory — " + (_REASON or "fallback")
    return backend, detail
