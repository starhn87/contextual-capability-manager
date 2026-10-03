"""Human-readable receipts for manager-mediated preparation and session cleanup."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict


def _cell(value: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:120]
    for original, escaped in (("\\", "\\\\"), ("|", "\\|"), ("[", "\\["),
                              ("]", "\\]"), ("`", "\\`"), ("*", "\\*"), ("_", "\\_"),
                              ("<", "&lt;"), (">", "&gt;")):
        text = text.replace(original, escaped)
    return text


def markdown(summary: Dict[str, Any]) -> str:
    if not summary["capabilities"]:
        return "이 세션에서 관리자를 통해 추가한 능력: 없음."
    lines = ["**세션 능력 요약**", "", "| 능력 | 종류 | 준비 | 사용 결과 | 세션 권한 | 캐시 |",
             "| --- | --- | --- | --- | --- | --- |"]
    kinds = {"skill": "스킬", "plugin": "플러그인", "connector": "커넥터", "unknown": "기록 없음"}
    packages = {"installed": "새로 설치", "cache_reused": "캐시 재사용",
                "not_prepared": "설치 안 됨", "unknown": "이전 기록 없음"}
    access = {"active": "사용 중", "released": "해제 완료", "expired": "만료",
              "inactive": "활성화 안 됨"}
    for item in summary["capabilities"]:
        if item["availability"] in ("unavailable", "failed", "requires_review"):
            usage = "사용 불가" if item["availability"] != "requires_review" else "검토 필요"
        elif item["reported_result"] == "success":
            usage = "성공 보고"
        elif item["reported_result"] == "failure":
            usage = "실패 보고"
        elif item["tool_successes"]:
            usage = "도구 호출 " + str(item["tool_successes"]) + "회"
        elif item["delivery_type"] == "static_skill":
            usage = "지침 전달 · 결과 미확인"
        else:
            usage = "준비됨 · 결과 미확인"
        if item["availability"] == "partial":
            usage += " · 일부 준비"
        if item["tool_errors"]:
            usage += " · 호출 오류 " + str(item["tool_errors"]) + "회"
        cached = item.get("cache_retained")
        lines.append("| " + " | ".join(_cell(value) for value in (
            item["name"], kinds.get(item["kind"], "기록 없음"),
            packages.get(item["package_state"], "기록 없음"), usage,
            access[item["access_state"]], "보관" if cached is True else "없음" if cached is False else "기록 없음")) + " |")
    lines += ["", "해제는 이 세션의 임시 권한 종료입니다. 패키지 캐시 삭제와는 별개입니다."]
    if summary.get("cleanup_errors"):
        lines += ["", "권한은 해제했지만 일부 연결 종료를 확인하지 못했습니다."]
    return "\n".join(lines)


def receipt(store, session_id: str, directory: Path) -> Dict[str, Any]:
    summary = store.session_summary(session_id)
    for item in summary["capabilities"]:
        item["cache_retained"] = (None if item["kind"] == "unknown" else
                                  (directory / "cache" / item["id"] / item["version"]).is_dir())
    summary["summary_markdown"] = markdown(summary)
    return summary


def save_receipt(summary: Dict[str, Any], directory: Path) -> Dict[str, str]:
    key = hashlib.sha256(summary["session_id"].encode("utf-8")).hexdigest()[:24]
    target = directory / "session-summaries" / key
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = {}
    for extension, content in (("json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n"),
                               ("md", summary["summary_markdown"] + "\n")):
        destination = target / ("summary." + extension)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(target), delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            os.replace(str(temporary), str(destination))
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        result[extension] = str(destination)
    return result
