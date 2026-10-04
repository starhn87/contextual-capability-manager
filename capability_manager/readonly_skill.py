"""Read approved local skill text without preparation, telemetry, or network calls."""

from pathlib import Path
from typing import Any, Dict

from .catalog import Entry
from .decision import lexical_decision
from .installer import check_static_skill
from .instructions import read_skills
from .policy import Policy, within
from .session_summary import _cell


def read_static_skill(catalog: Dict[str, Entry], policy: Policy, cache_dir: Path, task: str,
                      context: str = "") -> Dict[str, Any]:
    if not isinstance(task, str) or not task.strip() or len(task) > 2000:
        raise ValueError("task must contain 1-2000 characters")
    if not isinstance(context, str) or len(context) > 1000:
        raise ValueError("context must contain at most 1000 characters")
    local = {}
    unavailable = []
    for entry in catalog.values():
        try:
            policy.check_entry(entry)
            if entry.kind not in ("skill", "plugin"):
                continue
            retained = cache_dir / entry.id / entry.version
            cached = retained.is_dir()
            if not cached and entry.source["type"] != "directory":
                unavailable.append({"id": entry.id, "reason": "requires_preparation"})
                continue
            package = retained if cached else entry.catalog_path.parent / entry.source["path"]
            if cached and not within(package.resolve(), cache_dir.resolve()):
                raise PermissionError("cached package escapes the manager cache")
            if not package.is_dir():
                unavailable.append({"id": entry.id, "reason": "requires_preparation"})
                continue
            if package.is_symlink():
                raise ValueError("skill source is a symlink")
            check_static_skill(package)
            local[entry.id] = (entry, package, cached)
        except (OSError, ValueError, PermissionError) as exc:
            unavailable.append({"id": entry.id, "reason": type(exc).__name__})
    decision = lexical_decision(task, [item[0] for item in local.values()], context)
    result = {"status": "no_local_static_match", "decision": decision,
              "unavailable": unavailable, "scope": "read-only-call",
              "effects": {"packages_prepared": 0, "permissions_created": 0,
                          "files_written": 0, "network_requests": 0}}
    chosen = decision["recommendation"]
    if not chosen:
        return result
    entry, package, cached = local[chosen]
    policy.check_entry(entry)
    check_static_skill(package)
    instructions = read_skills(package, task + " " + context)
    if not instructions["skills"]:
        return result
    source = "기존 캐시 읽기" if cached else "로컬 지침 읽기"
    cache = "기존 보관" if cached else "생성 없음"
    text = "\n".join([
        "**이번 지침 조회 요약**", "",
        "추가 설치 0 · 캐시 재사용 " + str(int(cached)) +
        " · 권한 해제 0 · 활성 권한 0", "",
        "| 능력 | 종류 | 준비 | 사용 결과 | 추가 권한 | 캐시 |",
        "| --- | --- | --- | --- | --- | --- |",
        "| " + _cell(entry.name) + " | 스킬 지침 | " + source +
        " | 지침 전달 · 결과 미확인 | 생성 없음 | " + cache + " |", "",
        "이 조회는 설치·다운로드·권한 생성을 하지 않았습니다. "
        "앞선 설치나 활성 권한의 세션 합계는 별도 관리자 영수증을 확인합니다."])
    result.update({"status": "instructions_ready",
                   "capability": {**entry.summary(), **instructions},
                   "instruction_source": "retained_cache" if cached else "local_source",
                   "summary_markdown": text})
    return result
