# Contextual Capability Manager

작업에 필요한 스킬·플러그인을 선택하고 승인된 범위에서 설치합니다. 지원되는 커넥터는 필요한 계정 로그인만 요청한 뒤, 도구 연결을 확인하고 같은 대화의 원래 작업을 이어갑니다. 답변 끝에서 설치·재사용·세션 권한 정리 내역을 확인할 수 있습니다.

## 설치

처음 한 번 **마켓플레이스를 등록하고 플러그인을 설치**합니다. `marketplace add`는 목록 등록이며, 다음 명령이 실제 플러그인 설치입니다.

### Codex

```bash
codex plugin marketplace add starhn87/contextual-capability-manager
codex plugin add contextual-capability-manager@contextual-capabilities
```

### Claude Code

```bash
claude plugin marketplace add starhn87/contextual-capability-manager
CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1 claude plugin install contextual-capability-manager@contextual-capabilities
```

설치 후 앱·CLI를 다시 시작하고 새 세션을 엽니다.

Codex의 자동 세션 연결·요청 관찰·종료 정리를 사용하려면 `/hooks`를 열고, 실행 경로에 `contextual-capability-manager`가 포함된 아래 항목을 처음 한 번 **신뢰·활성화**합니다.

| `/hooks` 이벤트 | 실행 파일 |
| --- | --- |
| `SessionStart` | `hooks/session_start.py` |
| `UserPromptSubmit` | `hooks/user_prompt_submit.py` |
| `SessionEnd` | `hooks/session_end.py` |

정적 지침 조회와 추가 능력 0개 요약은 훅 설정 없이 사용할 수 있습니다. 목록에 위 항목이 없으면 [훅 등록·설정 안내](docs/advanced.md#실행)를 참고하세요.

## 사용

평소처럼 작업을 요청하세요. 필요한 능력이 생기면 등록된 출처에서 찾아 적용합니다.

스킬 이름을 지정할 필요는 없습니다. 등록된 Codex·Claude 마켓플레이스 목록은 자동으로 탐색하며, 조회 때 로컬 변경을 반영하고 등록된 HTTPS Git 목록은 백그라운드에서 갱신합니다. [탐색·갱신 범위](docs/advanced.md#능력-목록-자동-갱신).

```text
이 회의 내용을 우리 팀의 회의록 양식으로 정리해 줘.
```

답변 끝에는 사용한 능력과 설치·재사용·권한 해제 내역이 표시됩니다. 추가 능력을 사용하지 않은 경우의 예:

```text
추가 설치 0 · 캐시 재사용 0 · 권한 해제 0 · 활성 권한 0
```

권한 해제 후에도 패키지 캐시는 다음 작업을 위해 보관합니다. 원격 설치·실행 도구·새 계정 연결은 플랫폼의 승인·인증 절차를 따릅니다.

0.2.0의 Codex 자동 연결 경로는 **Linear 읽기 전용 작업**을 기본 지원합니다. 예를 들어 “내 Linear 프로젝트에서 이 개선안과 중복되는 이슈를 찾아줘”라고 요청하면 실제 네이티브 플러그인 설치 → OAuth 로그인 링크 → 콜백 수신 → 도구 조회 확인 → 이슈 검색 순서로 진행합니다. 별도의 “설치해줘”, “로그인 끝났어”, “원래 작업 다시 해줘” 요청은 필요하지 않습니다.

이 경로는 관리자 게이트웨이의 공식 Linear OAuth 연결을 사용합니다. Codex 앱의 계정 연결 표시나 네이티브 도구 목록이 즉시 갱신됐다고 주장하지 않습니다. 다른 OAuth MCP도 명시적 연결 설정으로 확장할 수 있으며, 연결 어댑터가 없는 앱 전용 플러그인은 플랫폼 활성화가 필요하다고 반환합니다. [지원 범위·설정](docs/advanced.md#네이티브-설치와-인증-재개).

## 자세히 보기

- [추가 출처 등록·상세 설정·로컬 개발](docs/advanced.md)
- [판단 검증 절차](evals/README.md)
- [Codex·Claude 설치본 검증 결과](evals/native-deployment-2026-10-05-0.1.14.md)
- [0.2.0 설치·인증·재개 소스 검증](evals/native-setup-2026-10-05-0.2.0.md)
