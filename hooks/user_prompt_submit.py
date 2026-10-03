"""Record a local capability judgment for every user prompt without storing its text."""

import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from capability_manager.manager import CapabilityManager
from capability_manager.runtime import storage_id, record_hook_failure


def main():
    event = json.load(sys.stdin)
    session_id = event.get("session_id")
    turn_id = event.get("turn_id") or secrets.token_hex(16)
    prompt = event.get("prompt")
    if not all(isinstance(value, str) and value for value in (session_id, turn_id, prompt)):
        return
    try:
        manager = CapabilityManager(include_codex_catalog=False)
        result = manager.observe_prompt(
            prompt, session_id, turn_id, event.get("cwd", "")
        )
    except Exception as exc:
        record_hook_failure("UserPromptSubmit", session_id, exc, Path(__file__).resolve().parent.parent)
        print(json.dumps({"systemMessage": "Capability manager prompt observation failed (" +
                          type(exc).__name__ + "). This request was not observed."}))
        return
    candidate = result.get("suggested_capability_id")
    guidance = ("Capability manager turn_id: " + turn_id + ". Capability manager storage_id: " +
                storage_id(manager.data_dir) + ". Pass it as expected_storage_id. ")
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
