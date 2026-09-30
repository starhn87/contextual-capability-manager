"""Record a local capability judgment for every user prompt without storing its text."""

import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from capability_manager.manager import CapabilityManager


def main():
    event = json.load(sys.stdin)
    session_id = event.get("session_id")
    turn_id = event.get("turn_id") or secrets.token_hex(16)
    prompt = event.get("prompt")
    if not all(isinstance(value, str) and value for value in (session_id, turn_id, prompt)):
        return
    try:
        result = CapabilityManager(include_codex_catalog=False).observe_prompt(
            prompt, session_id, turn_id, event.get("cwd", "")
        )
    except Exception:
        return
    candidate = result.get("suggested_capability_id")
    guidance = "Capability manager turn_id: " + turn_id + ". "
    if candidate:
        if result.get("need_reason") == "project_specific_task":
            guidance += (
                "The request depends on a project-specific format or resource. Check whether "
                "its rules are already available; if not, resolve the suggested capability "
                "before drafting. Do not present a generic format as the team's official one. "
            )
        else:
            guidance += "The user requested an additional capability. "
        guidance += ("Local capability check suggests " + candidate +
                     ". Use the capability manager with this turn_id if needed; policy is enforced there.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": guidance
    }}))


if __name__ == "__main__":
    main()
