# Claude Code 0.1.8 새 세션 검증 — 2026-10-04

설치된 플러그인의 자동 로딩, 시작·프롬프트 훅, 종료 훅의 권한 해제와 요약 저장을 실제 Claude Code CLI에서 확인했습니다. 모델의 스킬 사용과 최종 답변 요약표는 OAuth 로그인 만료로 검증하지 못했습니다. 전체 흐름이 성공한 실행으로 집계하지 않습니다.

## 실행 조건

- Claude Code CLI: `2.1.282`.
- 플러그인: 사용자 범위에 설치·활성화된 `contextual-capability-manager@contextual-capabilities`, 버전 `0.1.8`.
- `--plugin-dir`나 임시 MCP 등록 없이 설치된 플러그인을 그대로 로드했습니다.
- `claude -p --output-format stream-json --verbose --include-hook-events`로 새 UUID 세션 두 개를 실행했습니다.
- 권한 모드는 `manual`, 비대화형 승인 응답은 `none`으로 두고, 기본 제공 도구는 `Skill,ToolSearch`로 제한했습니다. 전체 권한 우회나 영구 승인 설정 변경은 하지 않았습니다.
- 합성 회의 내용과 정적 스킬 한 개만 포함하는 로컬 카탈로그·정책을 사용했습니다. `CAPMGR_DATA_DIR`, `CAPMGR_CATALOGS`, `CAPMGR_POLICY`로 임시 경로를 지정했습니다. 등록 마켓플레이스 탐색과 원격 판단기는 이 검증에서 사용하지 않았습니다.
- 실사용 DB에 시험 기록을 섞지 않았습니다. 종료 후 증거를 추출하고 임시 DB·스킬 캐시·작업 폴더를 삭제했습니다.

## 관찰 결과

| 관찰 항목 | 준비한 능력이 없는 새 세션 | 정적 스킬을 미리 준비한 새 세션 |
| --- | --- | --- |
| 설치된 관리자 MCP 서버 | `connected` | `connected` |
| 등록된 관리자 MCP 도구 | 13개 | 13개 |
| 포함 스킬 | `contextual-capability-manager:adaptive-capabilities` | 동일 |
| 시작 훅 | 성공, 세션 연결 1건 | 성공, 세션 연결 1건 |
| 프롬프트 훅 | 성공, 관찰 1건 | 성공, 관찰 1건 |
| 검증 준비 단계의 정적 스킬 | 0개 | 1개 |
| 모델의 관리자 도구 호출 | 0회 | 0회 |
| 종료 후 활성 권한 | 0개 | 0개 |
| 종료 후 남은 세션 맥락 | 0개 | 0개 |
| 종료 요약 파일 | 저장됨 | 저장됨, 해제 1개·캐시 보관 |
| 정리 오류 | 없음 | 없음 |
| CLI 종료 코드 | 1, 인증 실패 | 1, 인증 실패 |
| 최종 답변에 요약표 표시 | 인증 실패로 미검증 | 인증 실패로 미검증 |

두 번째 사례의 스킬은 설치된 패키지의 `resolve_static` API를 검증 준비 단계에서 호출해 활성화했습니다. Claude 모델이 스킬을 찾아 적용한 것으로 계산하지 않습니다. 명시적 release는 호출하지 않고 CLI의 자연 종료에 따른 SessionEnd 정리를 확인했습니다.

시작·프롬프트 훅은 출력 스트림의 `hook_response`에서 `outcome=success`, `exit_code=0`으로 확인했습니다. 종료 정리는 종료 후 DB의 `capability_released`, `session_released` 이벤트와 저장된 `summary.json`에서 확인했습니다.

## 저장된 종료 요약

미리 준비한 정적 스킬 사례는 다음 표를 파일에 저장했습니다. 모델의 최종 답변에 나타난 표는 아닙니다.

| 능력 | 종류 | 준비 | 사용 결과 | 세션 권한 | 캐시 |
| --- | --- | --- | --- | --- | --- |
| Verification Notes Skill | 스킬 | 새로 설치 | 지침 전달 · 결과 미확인 | 해제 완료 | 보관 |

요약의 `release_completed=true`, `active=0`, `released=1`, `cleanup_errors=[]`, `cache_retained=true`를 확인했습니다. 캐시 보관은 종료 훅 직후의 상태입니다. 이후 검증 도구가 임시 저장소 전체를 삭제했습니다.

## 남은 검증

모델 실행은 두 번 모두 다음 오류로 중단됐습니다.

    Failed to authenticate: OAuth session expired and could not be refreshed

`claude auth status --json`도 `loggedIn=false`, `authMethod=none`을 반환했습니다. CLI가 보고한 모델 비용은 두 실행 모두 0 USD입니다.

결과 이벤트의 `subtype`은 `success`였지만 `is_error=true`이고 프로세스 종료 코드도 1이므로 성공으로 해석하면 안 됩니다. 권한 거부도 발생하지 않았습니다. 모델이 도구를 요청하기 전에 인증에서 중단됐으므로 도구 승인 정책의 통과 여부는 이 실행으로 판단하지 않습니다.

재로그인 후 새 세션에서 스킬 탐색·지침 적용·명시적 결과 보고·release 호출·최종 답변 요약표를 이어서 확인해야 합니다. 필요한 검증용 도구는 해당 실행에만 좁게 허용하고, 실제 도구 결과와 최종 답변을 함께 확인합니다.

로그인 만료 복구 방법은 [Claude Code 인증 문서](https://code.claude.com/docs/en/authentication#renew-an-expiring-login)에 있습니다.

이번 검증에서 실제 네이티브 플러그인·커넥터의 신규 설치나 제거는 없었습니다. 기존 관리자 플러그인은 유지했고 검증용 임시 스킬 캐시만 삭제했습니다.
