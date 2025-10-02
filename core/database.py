# core/database.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Iterable, Tuple
from threading import RLock
import os
import uuid
import datetime as _dt

from .config import settings

# ===================== Estado global do backend =====================
_BACKEND = os.getenv("DB_BACKEND", "").strip().lower() or "memory"

def get_backend() -> str:
    return _BACKEND

def set_backend(kind: str) -> None:
    global _BACKEND
    kind = (kind or "").strip().lower()
    _BACKEND = "mongo" if kind == "mongo" else "memory"

# Se houver credenciais de Mongo, defaulta para mongo
if settings.mongo_uri():
    _BACKEND = os.getenv("DB_BACKEND", _BACKEND)
    if _BACKEND not in ("memory", "mongo"):
        _BACKEND = "mongo"

# ===================== Implementação: Memória =====================
_STORE: Dict[str, List[Dict[str, Any]]] = {}
_LOCK = RLock()

def _match_simple(doc: Dict[str, Any], filt: Optional[Dict[str, Any]]) -> bool:
    if not filt:
        return True
    for k, v in filt.items():
        # suporte mínimo a $in
        if isinstance(v, dict) and "$in" in v:
            if doc.get(k) not in v["$in"]:
                return False
        else:
            if doc.get(k) != v:
                return False
    return True

def _get_nested(d: Dict[str, Any], dotted: str, default=None):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur

class MemoryCollection:
    def __init__(self, name: str):
        self.name = name
        _STORE.setdefault(name, [])

    def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            d = dict(doc)
            d.setdefault("_id", str(uuid.uuid4()))
            d.setdefault("ts", _dt.datetime.utcnow())
            _STORE[self.name].append(d)
            return {"inserted_id": d["_id"]}

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        with _LOCK:
            rows = [d.copy() for d in _STORE.get(self.name, []) if _match_simple(d, filt)]
        if sort:
            # aplica múltiplas chaves, da última para a primeira
            for key, direction in reversed(sort):
                rows.sort(
                    key=lambda x: _get_nested(x, key, None),
                    reverse=(direction or 1) < 0
                )
        if limit:
            rows = rows[:limit]
        return rows

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = list(self.find(filt=filt, sort=sort, limit=1))
        return rows[0] if rows else None

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            for d in rows:
                if _match_simple(d, filt):
                    if "$set" in update:
                        # suporta set com dotted paths simples
                        for k, v in update["$set"].items():
                            parts = k.split(".")
                            cur = d
                            for p in parts[:-1]:
                                if p not in cur or not isinstance(cur[p], dict):
                                    cur[p] = {}
                                cur = cur[p]
                            cur[parts[-1]] = v
                    else:
                        d.update(update)
                    return
            if upsert:
                doc = dict(filt)
                if "$set" in update:
                    # aplica $set
                    for k, v in update["$set"].items():
                        parts = k.split(".")
                        cur = doc
                        for p in parts[:-1]:
                            if p not in cur or not isinstance(cur[p], dict):
                                cur[p] = {}
                            cur = cur[p]
                        cur[parts[-1]] = v
                self.insert_one(doc)

    def delete_many(self, filt: Dict[str, Any]) -> int:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            before = len(rows)
            rows[:] = [d for d in rows if not _match_simple(d, filt)]
            return before - len(rows)

# ===================== Implementação: Mongo (opcional) =====================
_MONGO_OK = False
_mongo_client = None
_mongo_db = None

def _ensure_mongo():
    global _MONGO_OK, _mongo_client, _mongo_db
    if _mongo_db is not None:
        return
    uri = settings.mongo_uri()
    if not uri:
        _MONGO_OK = False
        return
    try:
        from pymongo import MongoClient
        _mongo_client = MongoClient(uri)
        _mongo_db = _mongo_client.get_database(settings.APP_NAME)
        _MONGO_OK = True
    except Exception:
        _MONGO_OK = False
        _mongo_client = None
        _mongo_db = None

class MongoCollection:
    def __init__(self, name: str):
        _ensure_mongo()
        if not _MONGO_OK:
            raise RuntimeError("Mongo não inicializado.")
        self._col = _mongo_db.get_collection(name)

    def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(doc)
        d.setdefault("ts", _dt.datetime.utcnow())
        r = self._col.insert_one(d)
        return {"inserted_id": str(r.inserted_id)}

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        cur = self._col.find(filt or {})
        if sort:
            cur = cur.sort(sort)
        if limit:
            cur = cur.limit(limit)
        return list(cur)

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if sort:
            cur = self._col.find(filt or {}).sort(sort).limit(1)
            rows = list(cur)
            return rows[0] if rows else None
        return self._col.find_one(filt or {})

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        self._col.update_one(filt, update, upsert=upsert)

    def delete_many(self, filt: Dict[str, Any]) -> int:
        return self._col.delete_many(filt or {}).deleted_count

# ===================== API pública =====================
def get_col(name: str):
    """Retorna uma coleção de acordo com o backend atual."""
    if get_backend() == "mongo":
        try:
            return MongoCollection(name)
        except Exception:
            # fallback duro para memória se Mongo falhar
            return MemoryCollection(name)
    return MemoryCollection(name)

def db_status() -> Tuple[str, str]:
    """(backend, detalhe)"""
    b = get_backend()
    if b == "mongo":
        _ensure_mongo()
        return ("mongo", "OK" if _MONGO_OK else "indisponível")
    return ("memory", "memória local")

def ping_db() -> Tuple[str, bool, str]:
    """(backend, ok, detalhe) — faz um insert+read+delete na coleção 'diagnostic'."""
    b = get_backend()
    try:
        col = get_col("diagnostic")
        rid = col.insert_one({"marker": "ping", "ts": _dt.datetime.utcnow()}).get("inserted_id")
        last = col.find_one(sort=[("ts", -1)])
        col.delete_many({"marker": "ping"})
        return (b, True, f"OK (id={rid}) — last_ts={last.get('ts') if last else '—'}")
    except Exception as e:
        return (b, False, f"{type(e).__name__}: {e}")
