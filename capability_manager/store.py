import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class Store:
    def __init__(self, path: Path, platform: str = "unknown",
                 plugin_version: str = "unknown"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.platform = platform
        self.plugin_version = plugin_version
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS active (
                    session_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    lease_until INTEGER NOT NULL,
                    PRIMARY KEY(session_id, capability_id)
                );
                CREATE TABLE IF NOT EXISTS session_contexts (
                    session_id TEXT PRIMARY KEY,
                    context_key TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outcomes (
                    context_key TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    session_id TEXT,
                    success INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    task_hash TEXT NOT NULL,
                    task_text TEXT,
                    backend TEXT NOT NULL,
                    model TEXT,
                    recommendation TEXT,
                    candidates_json TEXT NOT NULL,
                    need_probability REAL,
                    confidence REAL,
                    activation_status TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_feedback (
                    decision_id TEXT PRIMARY KEY REFERENCES decisions(id),
                    correct_capability_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS prompt_observations (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL REFERENCES decisions(id),
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capability_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT,
                    decision_id TEXT,
                    capability_id TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    plugin_version TEXT NOT NULL,
                    tool_name TEXT,
                    error_type TEXT,
                    duration_ms INTEGER,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS capability_events_session
                    ON capability_events(session_id, created_at);
                CREATE TABLE IF NOT EXISTS session_capabilities (
                    session_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    version TEXT NOT NULL,
                    package_state TEXT NOT NULL,
                    availability TEXT NOT NULL,
                    delivery_type TEXT NOT NULL,
                    first_prepared_at INTEGER NOT NULL,
                    last_prepared_at INTEGER NOT NULL,
                    released_at INTEGER,
                    PRIMARY KEY(session_id, capability_id)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(outcomes)")}
            if "session_id" not in columns:
                db.execute("ALTER TABLE outcomes ADD COLUMN session_id TEXT")
                db.execute("UPDATE outcomes SET session_id='legacy-' || rowid, success=0")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS outcome_session ON outcomes"
                       "(context_key, capability_id, session_id)")

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def add_event(self, session_id: str, event_type: str, status: str,
                  turn_id: Optional[str] = None, decision_id: Optional[str] = None,
                  capability_id: Optional[str] = None, tool_name: Optional[str] = None,
                  error_type: Optional[str] = None,
                  duration_ms: Optional[int] = None) -> str:
        allowed = {"prompt_observed", "capability_searched", "capability_delivered",
                   "activation_failed", "tool_call", "outcome_reported",
                   "feedback_recorded", "session_released", "session_started",
                   "capability_prepared", "capability_released", "connection_cleanup"}
        if event_type not in allowed or not session_id:
            raise ValueError("invalid capability event")
        identifier = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("""INSERT INTO capability_events VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (identifier, session_id, turn_id, decision_id, capability_id,
                        event_type, status, self.platform, self.plugin_version,
                        tool_name, error_type, duration_ms, int(time.time())))
        return identifier

    def events(self, days: int = 30, limit: int = 100,
               session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        since = int(time.time()) - days * 86400
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""SELECT * FROM capability_events
                WHERE created_at>=? AND (? IS NULL OR session_id=?)
                ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (since, session_id, session_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def event_report(self, days: int = 30) -> Dict[str, Any]:
        since = int(time.time()) - days * 86400
        with self._connect() as db:
            rows = db.execute("""SELECT platform, plugin_version, event_type, status,
                COUNT(*) FROM capability_events WHERE created_at>=?
                GROUP BY platform, plugin_version, event_type, status""", (since,)).fetchall()
            stages = db.execute("""SELECT event_type, COUNT(DISTINCT session_id)
                FROM capability_events WHERE created_at>=? GROUP BY event_type""", (since,)).fetchall()
            sessions = db.execute("SELECT COUNT(DISTINCT session_id) FROM capability_events "
                                  "WHERE created_at>=?", (since,)).fetchone()[0]
            durations = db.execute("""SELECT event_type, COUNT(*), AVG(duration_ms) FROM capability_events
                WHERE created_at>=? AND duration_ms IS NOT NULL GROUP BY event_type""", (since,)).fetchall()
        return {"days": days, "events": sum(row[4] for row in rows),
                "unique_sessions": sessions,
                "unique_sessions_by_event": {event: count for event, count in stages},
                "latency_ms_by_event": {event: {"samples": count, "mean": round(mean, 2)}
                                        for event, count, mean in durations},
                "groups": [{"platform": platform, "plugin_version": version,
                            "event_type": event_type, "status": status, "count": count}
                           for platform, version, event_type, status, count in rows]}

    def activate(self, session_id: str, capability_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM active WHERE lease_until < ?", (int(time.time()),))
            db.execute(
                "INSERT OR REPLACE INTO active VALUES(?, ?, ?)",
                (session_id, capability_id, int(time.time()) + 86400),
            )

    def is_active(self, session_id: str, capability_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM active WHERE session_id=? AND capability_id=? AND lease_until>=?",
                (session_id, capability_id, int(time.time())),
            ).fetchone()
        return row is not None

    def deactivate(self, session_id: str, capability_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM active WHERE session_id=? AND capability_id=?",
                       (session_id, capability_id))

    def release(self, session_id: str) -> List[str]:
        with self._connect() as db:
            rows = db.execute("SELECT capability_id FROM active WHERE session_id=?", (session_id,)).fetchall()
            now = int(time.time())
            for (capability_id,) in rows:
                # Preserve a truthful unknown origin for leases created by older versions.
                db.execute("""INSERT OR IGNORE INTO session_capabilities
                    VALUES(?, ?, ?, 'unknown', 'unknown', 'unknown', 'ready', 'unknown', ?, ?, ?)""",
                    (session_id, capability_id, capability_id, now, now, now))
                db.execute("UPDATE session_capabilities SET released_at=? "
                           "WHERE session_id=? AND capability_id=?", (now, session_id, capability_id))
            db.execute("DELETE FROM active WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM session_contexts WHERE session_id=?", (session_id,))
        return [row[0] for row in rows]

    def record_preparation(self, session_id: str, metadata: Dict[str, Any],
                           package_state: str = "not_prepared") -> None:
        if package_state not in ("not_prepared", "installed", "cache_reused"):
            raise ValueError("invalid package preparation state")
        now = int(time.time())
        with self._connect() as db:
            db.execute("""INSERT INTO session_capabilities
                VALUES(?, ?, ?, ?, ?, ?, 'prepared', 'unknown', ?, ?, NULL)
                ON CONFLICT(session_id, capability_id) DO UPDATE SET
                    name=excluded.name, kind=excluded.kind, version=excluded.version,
                    package_state=CASE WHEN session_capabilities.package_state='installed'
                        THEN 'installed' WHEN excluded.package_state='not_prepared'
                        THEN session_capabilities.package_state ELSE excluded.package_state END,
                    first_prepared_at=CASE WHEN session_capabilities.kind='unknown'
                        THEN excluded.first_prepared_at ELSE session_capabilities.first_prepared_at END,
                    availability='prepared', delivery_type='unknown', released_at=NULL,
                    last_prepared_at=excluded.last_prepared_at""",
                (session_id, metadata["id"], metadata["name"], metadata["kind"], metadata["version"],
                 package_state, now, now))

    def mark_delivery(self, session_id: str, capability_id: str, availability: str,
                      delivery_type: str = "unknown") -> None:
        with self._connect() as db:
            db.execute("UPDATE session_capabilities SET availability=?, delivery_type=? "
                       "WHERE session_id=? AND capability_id=?",
                       (availability, delivery_type, session_id, capability_id))

    def session_summary(self, session_id: str) -> Dict[str, Any]:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            raise ValueError("invalid session id")
        now = int(time.time())
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""SELECT c.*, a.lease_until FROM session_capabilities c
                LEFT JOIN active a ON a.session_id=c.session_id AND a.capability_id=c.capability_id
                WHERE c.session_id=? ORDER BY c.first_prepared_at, c.capability_id""", (session_id,)).fetchall()
            legacy = db.execute("""SELECT session_id, capability_id, lease_until FROM active a
                WHERE session_id=? AND NOT EXISTS (SELECT 1 FROM session_capabilities c
                WHERE c.session_id=a.session_id AND c.capability_id=a.capability_id)""", (session_id,)).fetchall()
            events = db.execute("""SELECT capability_id, status, COUNT(*) AS count
                FROM capability_events WHERE session_id=? AND event_type='tool_call'
                GROUP BY capability_id, status""", (session_id,)).fetchall()
            outcomes = db.execute("""SELECT capability_id, success FROM outcomes WHERE session_id=?
                ORDER BY created_at DESC, rowid DESC""", (session_id,)).fetchall()
            released = db.execute("SELECT COUNT(*) FROM capability_events "
                                  "WHERE session_id=? AND event_type='session_released'", (session_id,)).fetchone()[0]
            cleanup_errors = db.execute("""SELECT capability_id, error_type FROM capability_events
                WHERE session_id=? AND event_type='connection_cleanup' AND status='error'
                ORDER BY created_at, rowid""", (session_id,)).fetchall()
        rows = [dict(row) for row in rows]
        rows.extend({**dict(row), "name": row["capability_id"], "kind": "unknown", "version": "unknown",
                     "package_state": "unknown", "availability": "ready", "delivery_type": "unknown",
                     "first_prepared_at": None, "last_prepared_at": None, "released_at": None}
                    for row in legacy)
        latest_outcomes = {}
        for row in outcomes:
            latest_outcomes.setdefault(row["capability_id"], "success" if row["success"] else "failure")
        capabilities = []
        for row in rows:
            item = dict(row)
            identifier = item.pop("capability_id")
            item.pop("session_id")
            lease = item.pop("lease_until")
            if item["kind"] == "unknown":
                item["first_prepared_at"] = item["last_prepared_at"] = None
            active = lease is not None and lease >= now
            item["id"] = identifier
            item["access_state"] = ("active" if active else "released" if item["released_at"] is not None
                                    else "expired" if lease is not None else "inactive")
            item["tool_successes"] = sum(event["count"] for event in events
                                         if event["capability_id"] == identifier and event["status"] == "completed")
            item["tool_errors"] = sum(event["count"] for event in events
                                      if event["capability_id"] == identifier and event["status"] != "completed")
            item["reported_result"] = latest_outcomes.get(identifier, "not_reported")
            capabilities.append(item)
        counts = {"capabilities": len(capabilities),
                  "new_packages": sum(item["package_state"] == "installed" for item in capabilities),
                  "cache_reused": sum(item["package_state"] == "cache_reused" for item in capabilities),
                  "active": sum(item["access_state"] == "active" for item in capabilities),
                  "released": sum(item["access_state"] == "released" for item in capabilities)}
        return {"session_id": session_id, "scope": "manager-mediated", "platform": self.platform,
                "plugin_version": self.plugin_version, "release_completed": released > 0 and not counts["active"],
                "counts": counts, "capabilities": capabilities,
                "cleanup_errors": [dict(row) for row in cleanup_errors]}

    def set_session_context(self, session_id: str, context_key: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO session_contexts VALUES(?, ?)",
                       (session_id, context_key))

    def session_context(self, session_id: str) -> Optional[str]:
        with self._connect() as db:
            row = db.execute("SELECT context_key FROM session_contexts WHERE session_id=?",
                             (session_id,)).fetchone()
        return row[0] if row else None

    def record(self, context_key: str, capability_id: str, session_id: str, success: bool) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO outcomes(context_key, capability_id, session_id, success, created_at)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(context_key, capability_id, session_id) DO UPDATE SET
                   success=excluded.success, created_at=excluded.created_at""",
                (context_key, capability_id, session_id, int(success), int(time.time())),
            )

    def warm_ids(self, context_key: str, minimum: int = 3) -> List[str]:
        since = int(time.time()) - 30 * 86400
        with self._connect() as db:
            rows = db.execute(
                """SELECT capability_id FROM outcomes
                   WHERE context_key=? AND success=1 AND created_at>=?
                   GROUP BY capability_id HAVING COUNT(*)>=?""",
                (context_key, since, minimum),
            ).fetchall()
        return [row[0] for row in rows]

    def add_decision(self, session_id: str, context_key: str, task_hash: str,
                     task_text: Optional[str], decision: Dict[str, Any],
                     candidates: List[Dict[str, Any]], status: str) -> str:
        decision_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                """INSERT INTO decisions VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision_id, session_id, context_key, task_hash, task_text,
                 decision["backend"], decision.get("model"), decision.get("recommendation"),
                 json.dumps(candidates, ensure_ascii=False), decision.get("need_probability"),
                 decision.get("confidence"), status, int(time.time())),
            )
        return decision_id

    def latest_decision_context(self, session_id: str, capability_id: str) -> Optional[str]:
        with self._connect() as db:
            row = db.execute(
                """SELECT context_key FROM decisions
                   WHERE session_id=? AND recommendation=?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (session_id, capability_id),
            ).fetchone()
        return row[0] if row else None

    def latest_decision_id(self, session_id: str, capability_id: str) -> Optional[str]:
        with self._connect() as db:
            row = db.execute(
                """SELECT id FROM decisions WHERE session_id=? AND recommendation=?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (session_id, capability_id),
            ).fetchone()
        return row[0] if row else None

    def mark_decision(self, decision_id: str, status: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE decisions SET activation_status=? WHERE id=?", (status, decision_id))

    def add_prompt_observation(self, turn_id: str, session_id: str,
                               decision_id: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO prompt_observations VALUES(?, ?, ?, ?)",
                       (turn_id, session_id, decision_id, int(time.time())))

    def prompt_observation(self, turn_id: str) -> Optional[str]:
        with self._connect() as db:
            row = db.execute("SELECT decision_id FROM prompt_observations WHERE turn_id=?",
                             (turn_id,)).fetchone()
        return row[0] if row else None

    def mark_prompt_searched(self, session_id: str, turn_id: Optional[str] = None) -> None:
        with self._connect() as db:
            if turn_id:
                row = db.execute(
                    "SELECT decision_id FROM prompt_observations WHERE session_id=? AND turn_id=?",
                    (session_id, turn_id),
                ).fetchone()
            else:
                row = db.execute(
                    """SELECT p.decision_id FROM prompt_observations p
                       JOIN decisions d ON d.id=p.decision_id
                       WHERE p.session_id=? AND p.created_at>=? AND d.activation_status='not_searched'
                       ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1""",
                    (session_id, int(time.time()) - 600),
                ).fetchone()
            if row:
                db.execute("UPDATE decisions SET activation_status='searched' WHERE id=?",
                           (row[0],))

    def get_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """SELECT d.*, f.correct_capability_id, f.source AS feedback_source
                   FROM decisions d LEFT JOIN decision_feedback f ON f.decision_id=d.id
                   WHERE d.id=?""", (decision_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["candidates"] = json.loads(item.pop("candidates_json"))
        return item

    def add_feedback(self, decision_id: str, correct_capability_id: str,
                     source: str = "user") -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO decision_feedback VALUES(?, ?, ?, ?)
                   ON CONFLICT(decision_id) DO UPDATE SET
                   correct_capability_id=excluded.correct_capability_id,
                   source=excluded.source, created_at=excluded.created_at""",
                (decision_id, correct_capability_id, source, int(time.time())),
            )

    def decisions(self, days: int = 30, limit: int = 50,
                  pending_only: bool = False) -> List[Dict[str, Any]]:
        since = int(time.time()) - days * 86400
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT d.id, d.session_id, d.context_key, d.task_hash, d.task_text, d.backend,
                          d.model, d.recommendation, d.candidates_json, d.need_probability,
                          d.confidence, d.activation_status, d.created_at,
                          f.correct_capability_id
                   FROM decisions d LEFT JOIN decision_feedback f ON f.decision_id=d.id
                   WHERE d.created_at>=? AND (?=0 OR f.decision_id IS NULL)
                   ORDER BY d.created_at DESC, d.rowid DESC LIMIT ?""",
                (since, int(pending_only), limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["candidates"] = json.loads(item.pop("candidates_json"))
            result.append(item)
        return result

    def decision_report(self, days: int = 30) -> Dict[str, Any]:
        since = int(time.time()) - days * 86400
        with self._connect() as db:
            rows = db.execute(
                """SELECT d.backend, d.model, d.recommendation, f.correct_capability_id,
                          d.need_probability
                   FROM decisions d LEFT JOIN decision_feedback f ON f.decision_id=d.id
                   WHERE d.created_at>=?""", (since,)
            ).fetchall()
        groups: Dict[str, Dict[str, Any]] = {}
        for backend, model, recommendation, label, need_probability in rows:
            key = backend + (":" + model if model else "")
            group = groups.setdefault(key, {
                "decisions": 0, "selected": 0, "labeled": 0, "correct": 0,
                "false_positives": 0, "false_negatives": 0, "wrong_capability": 0,
                "known_cases": 0, "known_correct": 0,
                "unknown_cases": 0, "unknown_need_detected": 0, "unknown_autoselections": 0,
                "need_true_positives": 0, "need_false_positives": 0,
                "need_false_negatives": 0, "need_true_negatives": 0,
            })
            group["decisions"] += 1
            group["selected"] += recommendation is not None
            if label is None:
                continue
            group["labeled"] += 1
            actual_need = label != "none"
            predicted_need = (need_probability or 0) >= 0.85
            need_metric = ("need_true_positives" if actual_need and predicted_need else
                           "need_false_negatives" if actual_need else
                           "need_false_positives" if predicted_need else "need_true_negatives")
            group[need_metric] += 1
            if label == "other":
                group["unknown_cases"] += 1
                group["unknown_need_detected"] += predicted_need
                group["unknown_autoselections"] += recommendation is not None
            elif label != "none":
                group["known_cases"] += 1
                group["known_correct"] += recommendation == label
            if recommendation == (None if label in ("none", "other") else label):
                group["correct"] += 1
            elif recommendation is None and label not in ("none", "other"):
                group["false_negatives"] += 1
            elif label == "none":
                group["false_positives"] += 1
            elif label != "other":
                group["wrong_capability"] += 1
        for group in groups.values():
            count = group["labeled"]
            group["accuracy"] = group["correct"] / count if count else None
            group["coverage"] = group["selected"] / group["decisions"]
            group["known_accuracy"] = (group["known_correct"] / group["known_cases"]
                                        if group["known_cases"] else None)
            precision_base = group["need_true_positives"] + group["need_false_positives"]
            recall_base = group["need_true_positives"] + group["need_false_negatives"]
            group["need_precision"] = group["need_true_positives"] / precision_base if precision_base else None
            group["need_recall"] = group["need_true_positives"] / recall_base if recall_base else None
        with self._connect() as db:
            observations = db.execute(
                """SELECT d.activation_status, f.correct_capability_id
                   FROM prompt_observations p JOIN decisions d ON d.id=p.decision_id
                   LEFT JOIN decision_feedback f ON f.decision_id=d.id
                   WHERE p.created_at>=?""", (since,)
            ).fetchall()
        skipped = [label for status, label in observations if status == "not_searched"]
        return {"days": days, "groups": groups, "prompt_observations": {
            "total": len(observations),
            "searched": sum(status == "searched" for status, _ in observations),
            "not_searched": len(skipped),
            "labeled_not_searched": sum(label is not None for label in skipped),
            "missed_capability": sum(label not in (None, "none", "other") for label in skipped),
        }}
