"""Setz's brain: long-term memory with semantic recall, and a small neural network that learns which leads reply.

Memory
    Facts, episodes and insights ("Café Kranz signed on 3 Oct", "your Tuesday emails get the most replies",
    "Lea at Brew Lab prefers WhatsApp") stored in SQLite with an embedding each. `recall()` finds what's relevant to
    a question by meaning, not keywords, and weighs importance and recency. `learn()` keeps it fed from partners,
    leads, stage changes, meetings, the knowledge base and the pipeline/planner analytics; you can also tell Setz
    "remember that ...". The voice assistant and the "Ask Setz" box put the recalled memories into every answer.

    Embeddings: fastembed (multilingual e5, CPU, English + German) if installed, else Ollama (`bge-m3`), else a
    built-in hashing embedder that works offline with no downloads. Switching embedders re-indexes automatically.

Neural network
    `LeadNet` is a small multilayer perceptron (features -> 8 tanh units -> reply probability) trained on your own
    outreach history: which contacted leads went on to reply. It needs no ML library, trains in about a second, is
    cross-validated (AUC) before it is trusted, and reports which signals matter. Its predictions rank outreach
    sequences and show as "reply chance" on the Pipeline tab.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import struct
import time
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .db import DB, utcnow

KINDS = ("fact", "preference", "episode", "insight", "conversation")
HASH_DIM = 512


# -- embeddings -------------------------------------------------------------------

def _hash_embed(text: str, dim: int = HASH_DIM) -> list[float]:
    """Feature-hashing embedder: words + character trigrams, signed buckets, log term frequency, L2-normalised."""
    words = re.findall(r"\w+", (text or "").casefold())
    feats: dict[str, float] = {}
    for w in words:
        feats["w:" + w] = feats.get("w:" + w, 0) + 1.0
        padded = f"<{w}>"
        for i in range(len(padded) - 2):
            g = "g:" + padded[i:i + 3]
            feats[g] = feats.get(g, 0) + 0.5
    for a, b in zip(words, words[1:]):
        feats[f"b:{a}_{b}"] = feats.get(f"b:{a}_{b}", 0) + 0.7
    vec = [0.0] * dim
    for f, c in feats.items():
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
        vec[h % dim] += (1 if (h >> 63) & 1 else -1) * (1 + math.log(c))
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Embedder:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.mode = cfg.get("brain.embedder", "auto")   # auto | fastembed | ollama | hash
        self._fe: Any = None
        self.name = self._pick()

    def _pick(self) -> str:
        if self.mode in ("auto", "fastembed"):
            try:
                from fastembed import TextEmbedding  # type: ignore

                model = self.cfg.get("brain.fastembed_model", "intfloat/multilingual-e5-small")
                self._fe = TextEmbedding(model)
                return "fastembed:" + model
            except Exception:
                if self.mode == "fastembed":
                    raise
        if self.mode in ("auto", "ollama"):
            model = self.cfg.get("brain.ollama_model", "bge-m3")
            try:
                self._ollama(["ping"], model)
                return "ollama:" + model
            except Exception:
                if self.mode == "ollama":
                    raise
        return f"hash:{HASH_DIM}"

    def _ollama(self, texts: list[str], model: str) -> list[list[float]]:
        import httpx

        url = self.cfg.get("llm.ollama_url", "http://localhost:11434")
        r = httpx.post(f"{url}/api/embed", json={"model": model, "input": texts}, timeout=60)
        r.raise_for_status()
        return [_norm(v) for v in r.json()["embeddings"]]

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        if self.name.startswith("fastembed:"):
            prefix = "query: " if query else "passage: "   # e5 models expect these prefixes
            return [_norm([float(x) for x in v]) for v in self._fe.embed([prefix + t for t in texts])]
        if self.name.startswith("ollama:"):
            return self._ollama(texts, self.name.split(":", 1)[1])
        return [_hash_embed(t) for t in texts]


_EMBEDDERS: dict[tuple, Embedder] = {}


def embedder(cfg: Config) -> Embedder:
    """One embedder per process and setting (loading a model per request would be slow)."""
    key = (cfg.get("brain.embedder", "auto"), cfg.get("brain.fastembed_model"), cfg.get("brain.ollama_model"),
           cfg.get("llm.ollama_url"))
    e = _EMBEDDERS.get(key)
    if e is None:
        e = _EMBEDDERS[key] = Embedder(cfg)
    return e


def _pack(v: list[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def _unpack(b: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(b) // 4}f", b))


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# -- memory ---------------------------------------------------------------------------

def _ensure(db: DB) -> None:
    db.conn.execute(
        "CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, kind TEXT, subject TEXT, "
        "text TEXT NOT NULL, source TEXT, importance REAL DEFAULT 0.5, created_at TEXT, updated_at TEXT, last_used_at TEXT, "
        "uses INTEGER DEFAULT 0, embedder TEXT, vec BLOB)")
    db.conn.execute("CREATE INDEX IF NOT EXISTS ix_memories_subject ON memories(subject)")
    db.conn.commit()


def remember(cfg: Config, db: DB, text: str, *, kind: str = "fact", subject: str = "", source: str = "manual",
             importance: float = 0.5, key: str | None = None) -> int:
    """Store (or update) a memory. Without a key, a near-identical memory is updated instead of duplicated."""
    _ensure(db)
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        raise ValueError("nothing to remember")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    emb = embedder(cfg)
    vec = emb.embed([text])[0]
    now = utcnow()
    if key is None:
        for m in _scan(cfg, db, subject=subject or None):
            if _dot(vec, m["_vec"]) > 0.95:
                key = m["key"]
                importance = max(importance, m["importance"])
                break
        else:
            key = f"{kind}:{hashlib.sha1(text.casefold().encode()).hexdigest()[:16]}"
    db.x(
        "INSERT INTO memories(key,kind,subject,text,source,importance,created_at,updated_at,embedder,vec) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET text=excluded.text, kind=excluded.kind, subject=excluded.subject, source=excluded.source, "
        "importance=max(memories.importance, excluded.importance), updated_at=excluded.updated_at, embedder=excluded.embedder, vec=excluded.vec",
        (key, kind, subject, text, source, float(importance), now, now, emb.name, _pack(vec)))
    return db.one("SELECT id FROM memories WHERE key=?", (key,))["id"]


def _scan(cfg: Config, db: DB, kind: str | None = None, subject: str | None = None) -> list[dict]:
    """All memories with vectors from the current embedder (re-embedding stale ones in batches)."""
    _ensure(db)
    emb = embedder(cfg)
    sql, params = "SELECT * FROM memories WHERE 1=1", []
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if subject:
        sql += " AND subject=?"
        params.append(subject)
    rows = [dict(r) for r in db.q(sql, params)]
    stale = [r for r in rows if r["embedder"] != emb.name or not r["vec"]]
    for i in range(0, len(stale), 64):
        chunk = stale[i:i + 64]
        for r, v in zip(chunk, emb.embed([r["text"] for r in chunk])):
            r["vec"], r["embedder"] = _pack(v), emb.name
            db.conn.execute("UPDATE memories SET vec=?, embedder=? WHERE id=?", (r["vec"], emb.name, r["id"]))
    if stale:
        db.conn.commit()
    for r in rows:
        r["_vec"] = _unpack(r["vec"])
    return rows


def _recency(iso: str | None) -> float:
    if not iso:
        return 0.0
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return math.exp(-(datetime.now(timezone.utc) - dt).total_seconds() / 86400 / 60)


def recall(cfg: Config, db: DB, query: str, k: int = 6, kind: str | None = None, min_score: float = 0.15,
           touch: bool = True) -> list[dict]:
    """The k memories most relevant to the query: meaning first, then importance and recency."""
    rows = _scan(cfg, db, kind=kind)
    if not rows or not query.strip():
        return []
    q = embedder(cfg).embed([query], query=True)[0]
    qwords = set(re.findall(r"\w{3,}", query.casefold()))
    out = []
    for r in rows:
        sim = _dot(q, r["_vec"])
        # names matter a lot in a CRM: an exact subject mention is a strong signal
        if r["subject"] and r["subject"].casefold() in query.casefold():
            sim += 0.25
        elif qwords & set(re.findall(r"\w{3,}", (r["subject"] or "").casefold())):
            sim += 0.1
        score = 0.72 * sim + 0.16 * (r["importance"] or 0.5) + 0.12 * _recency(r["updated_at"])
        if sim >= min_score:
            out.append({k2: v for k2, v in r.items() if k2 not in ("vec", "_vec")} | {"score": round(score, 3), "similarity": round(sim, 3)})
    out.sort(key=lambda m: -m["score"])
    out = out[:k]
    if touch and out:
        now = utcnow()
        db.conn.executemany("UPDATE memories SET uses=uses+1, last_used_at=? WHERE id=?", [(now, m["id"]) for m in out])
        db.conn.commit()
    return out


def forget(db: DB, memory_id: int) -> bool:
    _ensure(db)
    return db.x("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0


def stats(db: DB) -> dict:
    _ensure(db)
    by_kind = {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) n FROM memories GROUP BY kind")}
    row = db.one("SELECT COUNT(*) n, COUNT(DISTINCT subject) s, MAX(updated_at) u FROM memories")
    return {"memories": row["n"], "subjects": row["s"], "by_kind": by_kind, "updated_at": row["u"],
            "embedder": db.one("SELECT embedder FROM memories ORDER BY updated_at DESC LIMIT 1")["embedder"] if row["n"] else None}


def context_for(cfg: Config, db: DB, question: str, k: int = 6) -> str:
    """Recalled memories as prompt context."""
    mems = recall(cfg, db, question, k)
    return "\n".join(f"- ({m['kind']}{', ' + m['subject'] if m['subject'] else ''}) {m['text']}" for m in mems)


# -- learning from the rest of Setz -----------------------------------------------------------

def learn(cfg: Config, db: DB) -> dict:
    """Refresh memories from structured data (idempotent: keyed per record) and retrain the lead model."""
    _ensure(db)
    n = 0

    def put(key: str, text: str, kind: str, subject: str, source: str, importance: float) -> None:
        nonlocal n
        remember(cfg, db, text, kind=kind, subject=subject, source=source, importance=importance, key=key)
        n += 1

    from .knowledge import facts

    kb_keys: list[str] = []
    for line in [re.sub(r"\*\*|__", "", ln).strip("-* ").strip() for ln in facts().splitlines()]:
        if len(line) > 25 and not line.startswith(("#", "|")):
            kb_keys.append("kb:" + hashlib.sha1(line.encode()).hexdigest()[:12])
            put(kb_keys[-1], line, "fact", "Settleezy", "knowledge", 0.9)
    if kb_keys:   # knowledge base lines that were edited or removed are forgotten
        db.x(f"DELETE FROM memories WHERE key LIKE 'kb:%' AND key NOT IN ({','.join('?' * len(kb_keys))})", kb_keys)
    for p in db.q("SELECT * FROM partners"):
        txt = (f"{p['name']} is a {p['kind'] or 'partner'} partner, status {p['status']}, onboarding stage {p['stage']}."
               + (f" Member offer: {p['offer']}." if p["offer"] else "") + (f" Contact: {p['contact_name']}." if p["contact_name"] else "")
               + (f" Renewal {p['renewal_date']}." if p["renewal_date"] else "") + (f" {p['redemptions']} redemptions." if p["redemptions"] else ""))
        put(f"partner:{p['id']}", txt, "fact", p["name"], "partners", 0.75)
        if p["notes"]:
            put(f"partner-notes:{p['id']}", f"{p['name']}: {p['notes']}", "fact", p["name"], "partners", 0.7)
    for l in db.q("SELECT * FROM leads WHERE notes IS NOT NULL AND trim(notes) != ''"):
        put(f"lead-notes:{l['id']}", f"{l['name']} ({l['kind']}, {l['status']}): {l['notes']}", "fact", l["name"], "leads", 0.6)
    imp = {"replied": 0.55, "meeting": 0.65, "partner": 0.8, "lost": 0.5}
    for e in db.q("SELECT e.*, l.name, l.kind, l.campus FROM lead_events e JOIN leads l ON l.id=e.lead_id "
                  "WHERE e.to_status IN ('replied','meeting','partner','lost') ORDER BY e.at DESC LIMIT 400"):
        verb = {"replied": "replied to outreach", "meeting": "had a meeting with you", "partner": "became a partner",
                "lost": "was marked lost"}[e["to_status"]]
        put(f"ev:{e['id']}", f"{e['name']} ({e['kind']}{', near ' + e['campus'] if e['campus'] else ''}) {verb} on {e['at'][:10]}.",
            "episode", e["name"], "pipeline", imp[e["to_status"]])
    for m in db.q("SELECT * FROM events WHERE start >= date('now','-45 day') AND start <= datetime('now') ORDER BY start DESC LIMIT 80"):
        put(f"meeting:{m['id']}", f"Meeting '{m['title']}' on {m['start'][:10]}" + (f" with {m['attendees'][:120]}" if m["attendees"] else "") + ".",
            "episode", m["title"][:60], "calendar", 0.4)
    try:
        from .pipeline import conversion

        for c in conversion(db):
            if not c["assumed"]:
                put(f"insight:conv:{c['step']}", f"Your {c['step']} conversion is {c['rate']:.0%} over {c['sample']} leads"
                    + (f", taking a median {c['median_days']} days." if c["median_days"] is not None else "."), "insight", "pipeline", "pipeline", 0.7)
        from .planner import best_outreach_days

        days = best_outreach_days(cfg, db)
        if len(days) >= 2:
            b = max(days, key=lambda d: d["reply_rate"])
            put("insight:best-day", f"Emails you send on {b['weekday']} get the most replies ({b['reply_rate']:.0%}).",
                "insight", "outreach", "planner", 0.7)
    except Exception:
        pass
    synth = json.loads(db.kv_get("setz:synthesis", "{}") or "{}")
    if synth.get("briefing"):
        put("setz:briefing", "Latest team briefing: " + synth["briefing"][:600], "insight", "Setz", "agents", 0.4)
    model = LeadNet.train_from_db(db)
    return {"memories_refreshed": n, "lead_model": model.summary() if model else "not enough outreach history yet"}


# -- neural lead model ---------------------------------------------------------------------------

LEAD_KINDS = ["merchant", "brand", "university", "housing", "service"]
FEATURES = [*(f"kind:{k}" for k in LEAD_KINDS), "category value", "has email", "has Instagram", "has website", "has phone",
            "near campus", "walking distance", "on competitor sites", "base score"]


def features(lead: dict) -> list[float]:
    from .leads import category_weight

    comp = [s for s in json.loads(lead.get("sources") or "[]") if not s.startswith(("osm:", "seed:", "import"))]
    d = lead.get("distance_m")
    return [*(1.0 if lead.get("kind") == k else 0.0 for k in LEAD_KINDS),
            category_weight(f"{lead.get('category') or ''} {lead.get('name') or ''}"),
            1.0 if lead.get("email") else 0.0, 1.0 if lead.get("instagram") else 0.0,
            1.0 if lead.get("website") else 0.0, 1.0 if lead.get("phone") else 0.0,
            1.0 if d is not None else 0.0, (1 - min(d, 2000) / 2000) if d is not None else 0.0,
            min(len(comp), 3) / 3, (lead.get("score") or 0) / 100]


def _sig(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def auc(y: list[int], p: list[float]) -> float | None:
    pos = [s for s, t in zip(p, y) if t]
    neg = [s for s, t in zip(p, y) if not t]
    if not pos or not neg:
        return None
    wins = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


class LeadNet:
    """features -> hidden (tanh) -> sigmoid. Full-batch gradient descent with L2 and class weighting."""

    def __init__(self, n_in: int, hidden: int = 8, seed: int = 7):
        rng = random.Random(seed)
        s1, s2 = math.sqrt(2 / (n_in + hidden)), math.sqrt(2 / (hidden + 1))
        self.W1 = [[rng.gauss(0, s1) for _ in range(n_in)] for _ in range(hidden)]
        self.b1 = [0.0] * hidden
        self.W2 = [rng.gauss(0, s2) for _ in range(hidden)]
        self.b2 = 0.0
        self.meta: dict[str, Any] = {}

    def _forward(self, x: list[float]) -> tuple[list[float], float]:
        h = [math.tanh(sum(w * xi for w, xi in zip(row, x)) + b) for row, b in zip(self.W1, self.b1)]
        return h, _sig(sum(w * hi for w, hi in zip(self.W2, h)) + self.b2)

    def predict(self, x: list[float]) -> float:
        return self._forward(x)[1]

    def fit(self, X: list[list[float]], y: list[int], epochs: int = 300, lr: float = 0.3, l2: float = 1e-3) -> "LeadNet":
        n = len(X)
        pos = sum(y) or 1
        wpos, wneg = n / (2 * pos), n / (2 * max(1, n - pos))   # balance rare replies
        H = len(self.W1)
        for _ in range(epochs):
            gW1 = [[0.0] * len(X[0]) for _ in range(H)]
            gb1 = [0.0] * H
            gW2 = [0.0] * H
            gb2 = 0.0
            for x, t in zip(X, y):
                h, p = self._forward(x)
                d = (p - t) * (wpos if t else wneg)
                gb2 += d
                for j in range(H):
                    gW2[j] += d * h[j]
                    dh = d * self.W2[j] * (1 - h[j] * h[j])
                    gb1[j] += dh
                    row = gW1[j]
                    for i, xi in enumerate(x):
                        row[i] += dh * xi
            self.b2 -= lr * gb2 / n
            for j in range(H):
                self.W2[j] -= lr * (gW2[j] / n + l2 * self.W2[j])
                self.b1[j] -= lr * gb1[j] / n
                for i in range(len(X[0])):
                    self.W1[j][i] -= lr * (gW1[j][i] / n + l2 * self.W1[j][i])
        return self

    # -- persistence + training on the pipeline -----------------------------------------------
    def to_json(self) -> str:
        return json.dumps({"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2, "meta": self.meta})

    @classmethod
    def from_json(cls, s: str) -> "LeadNet":
        d = json.loads(s)
        net = cls(len(d["W1"][0]), len(d["W1"]))
        net.W1, net.b1, net.W2, net.b2, net.meta = d["W1"], d["b1"], d["W2"], d["b2"], d.get("meta", {})
        return net

    def summary(self) -> dict:
        return {k: self.meta.get(k) for k in ("n", "positives", "auc", "trained_at", "top_signals")}

    @staticmethod
    def training_set(db: DB) -> tuple[list[list[float]], list[int], list[int]]:
        """Contacted leads that had time to answer; label = they replied (or got further)."""
        from .pipeline import STALE_DAYS, _days_since, _reached

        reached = _reached(db)
        later = set(reached["replied"])
        X, y, ids = [], [], []
        for lid, at in reached["contacted"].items():
            if lid not in later and (_days_since(at) or 0) < STALE_DAYS["contacted"]:
                continue   # too fresh to call it a "no"
            row = db.one("SELECT * FROM leads WHERE id=?", (lid,))
            if row:
                X.append(features(dict(row)))
                y.append(1 if lid in later else 0)
                ids.append(lid)
        return X, y, ids

    @classmethod
    def train_from_db(cls, db: DB, min_samples: int = 20) -> "LeadNet | None":
        X, y, _ = cls.training_set(db)
        if len(X) < min_samples or sum(y) < 3 or sum(y) > len(y) - 3:
            return None
        epochs = 300 if len(X) < 400 else 150
        # 5-fold cross-validated AUC: honest estimate of how well it ranks leads it hasn't seen
        idx = list(range(len(X)))
        random.Random(3).shuffle(idx)
        folds = [idx[i::5] for i in range(5)]
        preds = [0.0] * len(X)
        for f in folds:
            test = set(f)
            tr = [i for i in idx if i not in test]
            net = cls(len(X[0])).fit([X[i] for i in tr], [y[i] for i in tr], epochs)
            for i in f:
                preds[i] = net.predict(X[i])
        cv_auc = auc(y, preds)
        net = cls(len(X[0])).fit(X, y, epochs)
        base = auc(y, [net.predict(x) for x in X]) or 0.5
        rng = random.Random(11)
        imp = []
        for j, name in enumerate(FEATURES):   # permutation importance
            col = [x[j] for x in X]
            rng.shuffle(col)
            a = auc(y, [net.predict(x[:j] + [col[i]] + x[j + 1:]) for i, x in enumerate(X)]) or 0.5
            imp.append((name, round(base - a, 3)))
        imp.sort(key=lambda t: -t[1])
        net.meta = {"n": len(X), "positives": sum(y), "auc": round(cv_auc, 3) if cv_auc is not None else None,
                    "trained_at": utcnow(), "features": FEATURES, "importance": imp,
                    "top_signals": [n for n, v in imp if v > 0.005][:4], "base_rate": round(sum(y) / len(y), 3)}
        db.kv_set("brain:leadnet", net.to_json())
        return net


_MODEL_CACHE: dict[str, Any] = {"raw": None, "net": None}


def lead_model(db: DB) -> LeadNet | None:
    raw = db.kv_get("brain:leadnet")
    if not raw:
        return None
    if raw != _MODEL_CACHE["raw"]:
        _MODEL_CACHE.update(raw=raw, net=LeadNet.from_json(raw))
    net = _MODEL_CACHE["net"]
    # a model that can't beat a coin flip on unseen leads isn't used
    return net if (net.meta.get("auc") or 0) >= 0.55 else None


def reply_chance(db: DB, lead: dict) -> float | None:
    net = lead_model(db)
    return round(net.predict(features(lead)), 3) if net else None


# -- graph for the command center ------------------------------------------------------------------

def graph(cfg: Config, db: DB, limit: int = 90) -> dict:
    """Memories (most important / most used) as neurons, linked to their subject and to similar memories."""
    rows = sorted(_scan(cfg, db), key=lambda r: -((r["importance"] or 0) + 0.05 * (r["uses"] or 0) + 0.3 * _recency(r["updated_at"])))[:limit]
    nodes = [{"id": f"m{r['id']}", "label": r["text"][:90], "kind": r["kind"], "subject": r["subject"],
              "weight": round((r["importance"] or 0.5) + min(r["uses"] or 0, 20) / 40, 2)} for r in rows]
    subjects = sorted({r["subject"] for r in rows if r["subject"]})
    nodes += [{"id": "s:" + s, "label": s, "kind": "subject", "weight": 0.6} for s in subjects]
    links = [{"source": f"m{r['id']}", "target": "s:" + r["subject"], "w": 1.0} for r in rows if r["subject"]]
    for i, a in enumerate(rows):
        sims = sorted(((_dot(a["_vec"], b["_vec"]), b["id"]) for b in rows[i + 1:]), reverse=True)[:2]
        links += [{"source": f"m{a['id']}", "target": f"m{bid}", "w": round(s, 2)} for s, bid in sims if s > 0.3]
    net = lead_model(db) or (LeadNet.from_json(db.kv_get("brain:leadnet")) if db.kv_get("brain:leadnet") else None)
    model = None
    if net:
        model = {**net.summary(), "importance": net.meta.get("importance"), "features": FEATURES,
                 "W1": [[round(w, 3) for w in row] for row in net.W1], "W2": [round(w, 3) for w in net.W2],
                 "trusted": lead_model(db) is not None, "base_rate": net.meta.get("base_rate")}
    return {"nodes": nodes, "links": links, "stats": stats(db), "model": model, "at": time.time()}
