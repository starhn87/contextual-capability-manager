# Contextual Capability Manager

작업에 필요한 스킬·MCP 도구를 찾아 승인된 범위에서 같은 대화에 적용합니다. 답변 끝에서 추가 설치·캐시 재사용·세션 권한 정리 내역을 확인할 수 있습니다.

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

Codex의 자동 요청 관찰·종료 정리를 사용하려면 `/hooks`에서 이 플러그인의 세 훅을 처음 한 번 신뢰·활성화합니다. 정적 지침 조회와 추가 능력 0개 요약은 훅 설정 없이 사용할 수 있습니다. [훅 설정 안내](docs/advanced.md#실행).

## 사용

평소처럼 작업을 요청하세요. 필요한 능력이 생기면 등록된 출처에서 찾아 적용합니다.

```text
회의록 요약 스킬을 찾아서 이 회의 내용을 정리해 줘.
```

답변 끝에는 사용한 능력과 설치·재사용·권한 해제 내역이 표시됩니다. 추가 능력을 사용하지 않은 경우의 예:

```text
추가 설치 0 · 캐시 재사용 0 · 권한 해제 0 · 활성 권한 0
```

권한 해제 후에도 패키지 캐시는 다음 작업을 위해 보관합니다. 원격 설치·실행 도구·새 계정 연결은 플랫폼의 승인·인증 절차를 따릅니다.

## 자세히 보기

- [추가 출처 등록·상세 설정·로컬 개발](docs/advanced.md)
- [판단 검증 절차](evals/README.md)
- [Codex·Claude 설치본 검증 결과](evals/native-deployment-2026-10-04-0.1.11.md)
