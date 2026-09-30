import json
import os
import re
from urllib.request import Request
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from .catalog import Entry
from .network import open_no_redirect
from .policy import Policy


def _terms(value: str):
    return set(re.findall(r"[\w가-힣]+", value.casefold()))


def lexical_rank(task: str, entries: List[Entry]) -> List[Tuple[Entry, float]]:
    query = _terms(task)
    results = []
    for entry in entries:
        target = " ".join([entry.name, entry.description] + entry.tags).casefold()
        match = len(query & _terms(target))
        substring = sum(1 for tag in entry.tags if len(tag) >= 3 and tag.casefold() in task.casefold())
        score = match + 2 * substring
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
    r"|(?:스킬|플러그인|커넥터|추가\s*(?:능력|도구)).{0,16}(?:쓰지|사용하지|불필요)",
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


def lexical_decision(task: str, entries: List[Entry], context: str = "") -> Dict[str, object]:
    """Conservative local need gate followed by a distinct candidate choice."""
    ranked = lexical_rank(task + " " + context, entries)
    rejected = bool(CAPABILITY_REJECTION.search(task))
    requested = (bool(CAPABILITY_REQUEST.search(task))
                 and bool(CAPABILITY_ACTION.search(task)) and not rejected)
    top_score = ranked[0][1] if ranked else 0.0
    runner_score = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = 1.0 if top_score >= 2 and top_score - runner_score >= 2 else 0.0
    inferred = (not rejected and not SUPPLIED_MATERIAL.search(task)
                and bool(PROJECT_REFERENCE.search(task))
                and bool(WORK_ACTION.search(task)) and confidence >= 0.85)
    need_probability = 1.0 if requested or inferred else 0.0
    winner = ranked[0][0].id if ranked else None
    return {
        "backend": "lexical",
        "need_probability": need_probability,
        "need_reason": ("explicit_capability_request" if requested else
                        "project_specific_task" if inferred else "no_clear_capability_gap"),
        "confidence": confidence,
        "recommendation": winner if need_probability >= 0.85 and confidence >= 0.85 else None,
        "candidates": [{"id": entry.id, "score": score} for entry, score in ranked[:8]],
    }


class DecisionRouter:
    """Optional Jev/System One ranking; policy checks remain deterministic."""

    def __init__(self, policy: Policy):
        self.policy = policy
        self.url = os.environ.get("CAPMGR_DECIDER_URL", "").strip()
        self.model = os.environ.get("CAPMGR_DECIDER_MODEL", "jev-latest")
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
        probabilities = chosen.get("probabilities", {})
        ranked = sorted(
            ({"id": e.id, "score": float(probabilities.get(e.id, 0))} for e in short),
            key=lambda item: -item["score"],
        )
        return {
            "backend": "system-one", "model": self.model,
            "need_probability": need_probability,
            "confidence": confidence,
            "recommendation": recommendation,
            "candidates": ranked[:8],
        }
