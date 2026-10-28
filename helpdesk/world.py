"""The mock company: services, incidents, changes, employees, tickets, policy and the knowledge base.

`World.load()` reads the static data once. Each conversation works on `world.fork()`, so actions taken in
one demo session (unlocking an account, granting access) don't leak into another.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rank_bm25 import BM25Okapi

from .config import DATA_DIR

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class Article:
    id: str
    title: str
    systems: list[str]
    symptoms: list[str]
    audience: str
    status: str
    updated: str
    body: str


def _parse_article(path: Path) -> Article:
    raw = path.read_text()
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front)
    return Article(id=meta["id"], title=meta["title"], systems=meta.get("systems", []),
                   symptoms=meta.get("symptoms", []), audience=meta.get("audience", "employee"),
                   status=meta.get("status", "active"), updated=str(meta.get("updated", "")), body=body.strip())


@dataclass
class World:
    services: list[dict]
    incidents: list[dict]
    changes: list[dict]
    users: dict[str, dict]
    tickets: list[dict]
    policy: dict
    articles: dict[str, Article]
    _index: BM25Okapi | None = None
    _index_ids: list[str] = field(default_factory=list)
    created_tickets: list[dict] = field(default_factory=list)
    approval_requests: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "World":
        read = lambda name: json.loads((data_dir / name).read_text())
        articles = {a.id: a for a in map(_parse_article, sorted((data_dir / "kb").glob("*.md")))}
        world = cls(
            services=read("services.json"), incidents=read("incidents.json"), changes=read("changes.json"),
            users={u["user_id"]: u for u in read("users.json")}, tickets=read("tickets.json"),
            policy=yaml.safe_load((data_dir / "policy.yaml").read_text()), articles=articles)
        world._build_index()
        return world

    def _build_index(self) -> None:
        # Only current, employee-facing articles are searchable; deprecated ones never reach the agent.
        searchable = [a for a in self.articles.values() if a.status == "active" and a.audience == "employee"]
        self._index_ids = [a.id for a in searchable]
        self._index = BM25Okapi([tokenize(f"{a.title} {a.title} {' '.join(a.systems)} {a.body}") for a in searchable])

    def fork(self) -> "World":
        """A copy whose mutable records can change without touching the shared base world."""
        twin = copy.copy(self)
        twin.users = copy.deepcopy(self.users)
        twin.incidents = copy.deepcopy(self.incidents)
        twin.created_tickets = []
        twin.approval_requests = []
        return twin

    # ---- lookups -------------------------------------------------------------------------------

    def service(self, name: str) -> dict | None:
        """Map a free-text mention ("the vpn", "SFDC") to a catalog service, or None."""
        text = name.lower().strip()
        for svc in self.services:
            if text == svc["name"].lower() or text in svc["aliases"]:
                return svc
        for svc in self.services:
            if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in svc["aliases"]):
                return svc
        return None

    def search_articles(self, query: str, system: str | None = None, k: int = 4) -> list[tuple[Article, float]]:
        scores = self._index.get_scores(tokenize(query))
        ranked = []
        for article_id, score in zip(self._index_ids, scores):
            article = self.articles[article_id]
            if system and system in article.systems:
                score += 2.0  # metadata boost: the article is about the system in question
            if score > 0:
                ranked.append((article, float(score)))
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked[:k]

    def search_tickets(self, query: str, system: str | None = None, k: int = 4) -> list[dict]:
        terms = set(tokenize(query))
        scored = []
        for t in self.tickets:
            text = set(tokenize(f"{t['summary']} {t['root_cause']} {t['symptom']}"))
            score = len(terms & text) + (3 if system and t["system"] == system else 0)
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [t for _, t in scored[:k]]

    def known_ticket_ids(self) -> set[str]:
        ids = {i["id"] for i in self.incidents} | {t["ticket_id"] for t in self.tickets}
        ids |= {t["ticket_id"] for t in self.created_tickets} | {r["request_id"] for r in self.approval_requests}
        return ids
