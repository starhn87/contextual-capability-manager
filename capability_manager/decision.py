import json
import os
import re
from urllib.request import Request
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .catalog import Entry
from .network import open_no_redirect
from .policy import Policy


def _terms(value: str):
    return set(re.findall(r"[\w가-힣]+", value.casefold().replace("-", " ")))


SEARCH_STOPWORDS = {
    "a", "an", "and", "for", "in", "of", "on", "our", "the", "to", "with",
    "add", "apply", "find", "install", "need", "use", "using", "want",
    "capability", "connector", "mcp", "plugin", "skill",
    "사용", "필요", "적용", "설치", "찾아", "스킬", "플러그인", "커넥터",
}


def lexical_rank(task: str, entries: List[Entry]) -> List[Tuple[Entry, float]]:
    query = _terms(task) - SEARCH_STOPWORDS
    results = []
    for entry in entries:
        name = _terms(entry.name) - SEARCH_STOPWORDS
        tags = _terms(" ".join(entry.tags)) - SEARCH_STOPWORDS
        description = _terms(entry.description) - SEARCH_STOPWORDS
        score = (6 * len(query & name) + 3 * len(query & tags) +
                 len(query & description))
        score += 2 * sum(1 for tag in entry.tags if re.search(r"[가-힣]", tag)
                         and len(tag) >= 3 and tag.casefold() in task.casefold())
        if entry.name.casefold().replace("-", " ") in task.casefold().replace("-", " "):
            score += 3
        if score:
            results.append((entry, float(score)))
    return sorted(results, key=lambda item: (-item[1], item[0].id))


CAPABILITY_REQUEST = re.compile(
    r"\b(?:skill|plugin|connector|capability|mcp)\b|스킬|플러그인|커넥터|추가\s*(?:능력|도구)",
    re.IGNORECASE,
)
CAPABILITY_ACTION = re.compile(
    r"\b(?:use|need|find|apply|install|connect|add|enable|recommend|suggest)\b"
    r"|사용|필요|찾|적용|설치|연결|추가|켜|추천|써",
    re.IGNORECASE,
)
CAPABILITY_REJECTION = re.compile(
    r"\b(?:without|no)\s+(?:a\s+)?(?:skill|plugin|connector|capability)\b"
    r"|\b(?:don't|do not)\s+(?:use|install|apply)\s+(?:a\s+)?"
    r"(?:skill|plugin|connector|capability)\b"
    r"|\b(?:don't|do not)\s+(?:install|use|apply)(?:\s+or\s+(?:install|use|apply))?\s+"
    r"(?:one|any|it)\b"
    r"|\b(?:don't|do not|never)\s+(?:install|connect|enable|add|use|apply)\s+"
    r"(?:anything|any\b|new\b|additional\b|a\s+connector|the\s+connector)"
    r"|(?:스킬|플러그인|커넥터|추가\s*(?:능력|도구))[^.!?\n]{0,32}"
    r"(?:쓰지|사용하지|설치하지|연결하지|추가하지|적용하지|켜지|불필요)"
    r"|(?:스킬|플러그인|커넥터)[^.!?\n]{0,20}(?:설치|연결|사용|추가|적용)\s*금지"
    r"|(?:설치|연결|추가)\s*하지\s*(?:말|마)",
    re.IGNORECASE,
)
PROJECT_REFERENCE = re.compile(
    r"\b(?:our|team|company|workspace|project|connected|approved|internal|required)\b"
    r"|우리|팀|회사|사내|프로젝트|승인된|지정된|표준|양식",
    re.IGNORECASE,
)
WORK_ACTION = re.compile(
    r"\b(?:put|turn|create|prepare|follow|show|look\s+up|read|use|apply|check|"
    r"fetch|retrieve|current|live|latest)\b"
    r"|정리|조회|확인|적용|만들|작성|찾|따라|현재|최신",
    re.IGNORECASE,
)
SUPPLIED_MATERIAL = re.compile(
    r"\b(?:pasted|provided|included|shown|attached)\s+(?:below|here|in\s+this\s+message)?\b"
    r"|아래에\s*(?:붙|제공|첨부)|첨부한|붙여둔|제공한",
    re.IGNORECASE,
)
EXPLANATION = re.compile(r"\b(?:explain|define|translate)\b|설명|뜻|의미|번역", re.IGNORECASE)
PRODUCTION_ACTION = re.compile(
    r"\b(?:create|prepare|turn|put|fill|summarize|follow)\b|정리|작성|만들|요약|따라",
    re.IGNORECASE,
)
EXTERNAL_LOOKUP = re.compile(
    r"\b(?:fetch|retrieve|look\s+up|query|check|show|read)\b|조회|가져와|불러와|확인",
    re.IGNORECASE,
)
LIVE_REFERENCE = re.compile(r"\b(?:current|latest|live)\b|현재|최신|실시간", re.IGNORECASE)


def has_supplied_material(task: str, entry: Optional[Entry] = None) -> bool:
    """Treat supplied identifiers separately from supplied answers to a lookup."""
    segments = [part for part in re.split(r"(?<=[.!?])\s+|\n", task)
                if SUPPLIED_MATERIAL.search(part)]
    if not segments:
        return False
    if entry is None or entry.kind != "connector" or not EXTERNAL_LOOKUP.search(task):
        return True
    task_rank = lexical_rank(task, [entry])
    minimum = max(2, task_rank[0][1] / 2) if task_rank else 2
    for segment in segments:
        ranked = lexical_rank(segment, [entry])
        if ranked and ranked[0][1] >= minimum:
            return True
    return False


def lexical_decision(task: str, entries: List[Entry], context: str = "") -> Dict[str, object]:
    """Conservative local need gate followed by a distinct candidate choice."""
    ranked = lexical_rank(task + " " + context, entries)
    rejected = bool(CAPABILITY_REJECTION.search(task))
    requested = (bool(CAPABILITY_REQUEST.search(task))
                 and bool(CAPABILITY_ACTION.search(task)) and not rejected)
    top_score = ranked[0][1] if ranked else 0.0
    runner_score = ranked[1][1] if len(ranked) > 1 else 0.0
    duplicate_purpose = (len(ranked) > 1 and
                         ranked[0][0].description == ranked[1][0].description and
                         set(ranked[0][0].tags) == set(ranked[1][0].tags))
    confidence = (1.0 if top_score >= 2 and top_score - runner_score >= 2
                  and not duplicate_purpose else 0.0)
    top = ranked[0][0] if ranked else None
    explanation_only = (bool(EXPLANATION.search(task)) and
                        not PRODUCTION_ACTION.search(task) and
                        not (top and top.kind == "connector" and EXTERNAL_LOOKUP.search(task)))
    # A description of using a skill is not itself a request to activate one.
    if explanation_only:
        requested = False
    live_lookup = (top is not None and top.kind == "connector" and
                   bool(EXTERNAL_LOOKUP.search(task)) and bool(LIVE_REFERENCE.search(task)))
    inferred = (not rejected and not explanation_only and not has_supplied_material(task, top)
                and (live_lookup or (bool(PROJECT_REFERENCE.search(task))
                                     and bool(WORK_ACTION.search(task))))
                and confidence >= 0.85)
    need_probability = 1.0 if requested or inferred else 0.0
    winner = ranked[0][0].id if ranked else None
    return {
        "backend": "lexical",
        "need_probability": need_probability,
        "need_reason": ("explicit_rejection" if rejected else
                        "explicit_capability_request" if requested else
                        "live_external_lookup" if inferred and live_lookup else
                        "project_specific_task" if inferred else "no_clear_capability_gap"),
        "confidence_kind": "heuristic",
        "confidence": confidence,
        "recommendation": winner if need_probability >= 0.85 and confidence >= 0.85 else None,
        "candidates": [{"id": entry.id, "score": score} for entry, score in ranked[:8]],
    }


class DecisionRouter:
    """Optional Jev/System One ranking; policy checks remain deterministic."""

    def __init__(self, policy: Policy, model: Optional[str] = None):
        self.policy = policy
        self.url = os.environ.get("CAPMGR_DECIDER_URL", "").strip()
        self.model = model or os.environ.get("CAPMGR_DECIDER_MODEL", "jev-latest")
        self.key_env = os.environ.get("CAPMGR_DECIDER_KEY_ENV", "TYPESAFE_API_KEY")

    def rank(self, task: str, entries: List[Entry], context: str = "") -> Dict[str, object]:
        baseline = lexical_rank(task + " " + context, entries)
        if not self.url:
            return lexical_decision(task, entries, context)
        parsed = urlparse(self.url)
        if parsed.scheme not in ("https", "http") or parsed.hostname not in self.policy.decision_hosts:
            raise PermissionError("decision endpoint is outside approved policy")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise PermissionError("remote decision endpoints require HTTPS")
        ranked_ids = {entry.id for entry, _ in baseline[:30]}
        short = [entry for entry, _ in baseline[:30]] + [
            entry for entry in entries if entry.id not in ranked_ids
        ][:20]
        criteria = {e.id: e.name + ": " + e.description for e in short}
        criteria["none"] = "No capability is needed for this task."
        body = {
            "model": self.model,
            "state": {"task": task, "context": context[:500]},
            "questions": {
                "needed": {
                    "type": "noul",
                    "instructions": "Is a new capability needed to complete this task well?"
                },
                "capability": {
                    "type": "choice",
                    "instructions": "Choose the single most useful capability, or none.",
                    "criteria": criteria,
                },
            },
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        secret = os.environ.get(self.key_env, "").strip()
        if secret:
            self.policy.check_secret_env(self.key_env)
            headers["Authorization"] = "Bearer " + secret
        request = Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with open_no_redirect(request, timeout=12) as response:
                result = json.load(response)
        except Exception as exc:
            return {"backend": "lexical-fallback", "error": type(exc).__name__,
                    "candidates": [{"id": e.id, "score": score} for e, score in baseline[:8]],
                    "recommendation": None}
        try:
            answers = result["answers"]
            chosen = answers["capability"]
            needed = answers["needed"]
            winner = chosen["choice"]
            confidence = float(chosen.get("confidence", 0))
            need_probability = float(needed.get("noul", 0))
        except (KeyError, TypeError, ValueError):
            return {"backend": "system-one-invalid", "recommendation": None,
                    "candidates": [{"id": e.id, "score": score} for e, score in baseline[:8]]}
        if winner not in criteria or winner == "none":
            winner = None
        recommendation = winner if confidence >= 0.85 and need_probability >= 0.85 else None
        rejected = bool(CAPABILITY_REJECTION.search(task))
        if rejected:
            recommendation, need_probability = None, 0.0
        probabilities = chosen.get("probabilities", {})
        ranked = sorted(
            ({"id": e.id, "score": float(probabilities.get(e.id, 0))} for e in short),
            key=lambda item: -item["score"],
        )
        return {
            "backend": "system-one", "model": self.model,
            "need_probability": need_probability,
            "confidence": confidence,
            "need_reason": "explicit_rejection" if rejected else "configured_decider",
            "recommendation": recommendation,
            "candidates": ranked[:8],
        }
