# 상세 설정과 개발 안내

기본 설치는 [README](../README.md#설치)를 참고하세요. 아래 개발·검증 명령은 저장소 루트에서 실행합니다.

## 실행

```bash
python3 -m unittest discover -s tests -v
python3 -m capability_manager.evaluation
python3 -m capability_manager.evaluation --cases evals/challenge.json
python3 -m capability_manager.evaluation --cases evals/boundaries.json
python3 -m capability_manager.cli --data-dir /private/tmp/capability-manager-demo \
  resolve 'summarize meeting notes' --session demo-1 --context sample-project
python3 -m capability_manager.cli --data-dir /private/tmp/capability-manager-demo \
  release --session demo-1
python3 -m capability_manager.cli --data-dir /private/tmp/capability-manager-demo \
  session-report --session demo-1
```

로컬 Codex 마켓플레이스 패키지를 만들려면 `python3 scripts/build_marketplace.py`를 실행합니다. 생성된 `dist/marketplace`에는 설치 가능한 플러그인과 마켓플레이스 목록이 들어 있습니다. 검토 후 `codex plugin marketplace add <절대 경로의 dist/marketplace>`와 `codex plugin add contextual-capability-manager@local-capabilities`로 설치할 수 있습니다. MCP와 포함 스킬은 설치 후 새 채팅에서 로드됩니다. **0.1.10의 Codex 정적 지침 조회는 훅 신뢰나 세션 ID 없이 동작합니다.** 자동 세션 연결·전체 프롬프트 관찰·종료 훅 정리는 여전히 추가 훅 검토·신뢰에 의존합니다. 관리자 세션 핸들과 연결 소유 권한 정리는 [설치 흐름 조사 보고서](../evals/codex-installation-friction-2026-10-04.md)의 후속 개선안으로 남아 있습니다. 관리자 플러그인은 작업 중 필요한 *다른* 능력을 MCP 게이트웨이를 통해 같은 채팅에 적용합니다.

시험 환경의 Codex CLI `0.158.0-alpha.2`와 `0.159.0`에서는 패키지에 포함된 훅이 목록에 표시되지 않았습니다. 검증 과정에서 `~/.codex/hooks.json`에 세 사용자 훅을 등록하고 플러그인의 `PLUGIN_DATA` 경로를 동일하게 지정했습니다. 최초 조회에서는 `untrusted`였고, 사용자가 `/hooks`에서 처리한 뒤 재조회했을 때 세 훅 모두 `enabled=true`, `trustStatus=trusted`였습니다. 사용자 훅 등록은 플러그인 설치만으로 자동 생성되지 않으며 패키지 훅 발견과 훅 신뢰는 별개 문제입니다. 훅의 모델용 안내에는 작업 경로와 사용자 요청 원문을 포함하지 않습니다.

**Codex에서 자동 세션 연결·요청 관찰·종료 정리를 사용하려면, 설치 후 한 번 `/hooks`에서 훅을 신뢰 처리해야 합니다.** 승인된 로컬·기존 캐시의 정적 지침 조회와 일반 답변의 0개 요약은 이 설정 없이 사용할 수 있습니다.

1. 터미널에서 `codex`를 실행한 뒤 대화 입력창에 `/hooks`를 입력합니다. `/hooks`는 Codex 안에서 사용하는 대화형 명령입니다.
2. 실행 경로에 `contextual-capability-manager`가 포함된 `SessionStart` (`hooks/session_start.py`), `UserPromptSubmit` (`hooks/user_prompt_submit.py`), `SessionEnd` (`hooks/session_end.py`)를 각각 선택해 실행 명령과 경로를 검토하고 **신뢰 처리**합니다. 세 훅이 모두 **활성 상태**인지도 확인합니다.
3. 앱·CLI를 다시 시작하고 새 세션을 엽니다.

한 번 신뢰한 훅은 **정의가 같으면 매 세션 다시 신뢰할 필요가 없습니다.** 업데이트로 실행 명령·경로 등 훅 정의가 변경되면 `/hooks`에서 다시 검토·신뢰합니다. 현재 검증한 Codex CLI `0.159.0`에는 세 훅을 한꺼번에 영구 신뢰 등록하는 비대화형 CLI 명령이 없습니다. [공식 훅 신뢰 안내](https://learn.chatgpt.com/docs/hooks).

`/hooks` 목록에 세 훅이 없으면 신뢰 처리 전에 훅 등록이 필요합니다. 위에서 확인한 CLI 버전처럼 패키지 훅을 발견하지 못하는 환경에서는 `~/.codex/hooks.json`에 사용자 훅을 등록해야 합니다. 각 명령은 설치된 플러그인의 `hooks/session_start.py`, `hooks/user_prompt_submit.py`, `hooks/session_end.py`를 실행하고, `CAPMGR_PLATFORM=codex`와 MCP의 `PLUGIN_DATA`와 같은 `CAPMGR_DATA_DIR`를 지정해야 합니다. 등록 후 `/hooks`를 다시 열어 신뢰·활성 상태를 확인하세요. [설치 흐름과 발견 문제](../evals/codex-installation-friction-2026-10-04.md).

Claude Code용 `.claude-plugin/plugin.json`, `.mcp.json`, 세션 훅도 포함합니다. 로컬 검증은 `claude --plugin-dir <이 저장소의 절대 경로>`로 시작할 수 있습니다. 사용자 범위 설치는 `claude plugin marketplace add starhn87/contextual-capability-manager`, `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1 claude plugin install contextual-capability-manager@contextual-capabilities`를 사용합니다. 새 세션에서 `/mcp`와 `/plugin`으로 로딩 상태를 확인하세요. Claude 훅에는 Codex의 `turn_id`가 없으므로 제출 때마다 ID를 생성해 판단 기록과 도구 호출을 연결합니다.

Claude Code `2.1.282`의 설치본 0.1.9는 추가 능력이 없는 요청의 자동 0개 표시와, 해당 검증 실행에 도구를 좁게 허용한 스킬 적용·사용 결과 기록·권한 해제·최종 답변 요약표를 확인했습니다. [0.1.9 실제 대화 검증 보고서](../evals/live-claude-2026-10-04-0.1.9.md)에 자동 표시와 도구 승인 조건, 검증 중 MCP 실패 캐시와 공식 재연결 후 복구를 구분해 기록했습니다. [0.1.8 검증 기록](../evals/live-claude-2026-10-04-0.1.8.md)도 보존합니다.

두 설치본을 각각 새 CLI 세션으로 다시 실행한 [Codex·Claude 비교 검증](../evals/live-both-2026-10-04.md)에서는 Claude 0.1.9의 두 표시 흐름을 재확인했습니다. 비교 시점의 Codex 설치본 0.1.8은 일반 요청의 요약 표시가 빠졌고, 기본 비대화형 승인 정책에서 스킬 준비·명시적 해제가 차단됐습니다. 훅 실행·저장소 일치·종료 정리와 도구 승인 결과를 구분해 기록했습니다.

0.1.10은 Codex의 실제 지침 부족에 `read_static_skill`을 먼저 사용합니다. 승인된 로컬 또는 기존 캐시의 정적 스킬을 로컬에서 선택해 읽으며, 다운로드·설치·DB 기록·실행·권한 생성을 하지 않습니다. 도구는 조회 범위의 요약표를 반환하고, 포함 스킬의 설명은 일반 답변의 0개 표시도 선택 대상으로 안내합니다. Claude에는 이 도구를 노출하지 않아 기존 준비·결과 기록·해제 경로를 유지합니다. [훅 없는 Codex 수정본 검증](../evals/codex-hook-free-2026-10-04-0.1.10.md)은 새 native CLI 세션에서 소스를 임시 로드한 결과입니다.

후속 **0.1.11은 양쪽 실제 설치본에 적용하고 새 세션 5건으로 검증했습니다.** 기존 Codex 훅이 세션 ID를 전달해도 능력 준비·활성화가 없으면 불필요한 해제를 호출하지 않습니다. 일반 요청의 0개 표시, 훅 없는 로컬 지침 조회표, Claude의 준비·권한 해제표를 확인했습니다. 기존 사용자는 앱·CLI를 다시 시작해 새 세션을 열면 됩니다. 버전 경로를 참조하는 수동 훅이 있으면 Codex 업데이트가 구버전 캐시를 삭제할 수 있으므로 원본 보존이나 정상 훅 재검토가 필요합니다. [실제 설치본 배포·검증과 정리 내역](../evals/native-deployment-2026-10-04-0.1.11.md).

Claude Code에서는 별도 목록 등록 없이 **이미 등록된 Claude 마켓플레이스**를 매 세션 검색합니다. 미설치 로컬 지침형 플러그인과 커밋 SHA가 고정된 HTTPS Git 플러그인을 후보로 읽고, 실제 필요할 때만 정적 스킬 파일을 세션 캐시에 복사해 같은 대화에 전달합니다. Git 후보에 MCP 서버나 훅이 있으면 자동 실행하지 않고 검토 대상으로 남깁니다. 이미 설치된 플러그인은 Claude가 직접 사용할 수 있으므로 중복 후보에서 제외합니다. 이 검색은 Claude 마켓플레이스 등록 목록을 사용하며, 마켓플레이스 자체를 새로 구독하거나 OAuth 계정을 연결하지는 않습니다. 판단 결과는 로컬에 기록합니다.

## 능력 목록 자동 갱신

0.1.12부터 Codex·Claude Code의 등록된 마켓플레이스를 기본으로 탐색합니다. 사용자가 스킬 이름을 지정하지 않아도 작업에 필요한 지침·도구를 선택하도록 포함 스킬과 MCP 안내를 제공합니다. 이미 설치된 네이티브 플러그인은 중복 준비하지 않습니다. Codex 목록의 원격 플러그인·계정 커넥터는 발견 정보로 포함하지만 플랫폼 설치·인증 없이 사용 가능하다고 표시하지 않습니다.

| 갱신 대상 | 시점과 범위 |
| --- | --- |
| 로컬 카탈로그·정책·알려진 마켓플레이스 파일 | 검색·정적 지침 조회·활성화·실행 시 다시 읽음 |
| 새 Codex 마켓플레이스 등록·설치 상태 | 서버 시작 시와 실행 중 60초 간격으로 native CLI 목록 조회 |
| Claude 등록 목록·설치 상태 | 조회 시 로컬 등록 파일을 다시 읽음; 외부 경로의 등록된 로컬 목록도 지원 |
| 등록된 HTTPS Git 마켓플레이스 | MCP 서버 실행 중 관리자 전용 스냅샷에 최대 6시간 간격으로 갱신; 실패 후 5분 간격 재시도 |

원격 갱신은 등록된 HTTPS Git 출처와 지정된 Git ref를 사용합니다. 기존 native 설치본·마켓플레이스 체크아웃·신뢰 기록을 업데이트하지 않으며, Git 훅·필터·서브모듈을 실행하거나 계정 인증을 시작하지 않습니다. 각 커밋의 복사본은 별도 경로에 보관합니다. 원격 Git 목록 복사본은 능력 준비·설치 영수증의 패키지 수에 포함하지 않습니다. 능력을 실제 준비하면 별도 캐시와 세션 권한 기록을 사용합니다.

네트워크 실패나 잘못된 새 목록은 마지막 정상 복사본을 유지하고 오류 종류를 표시합니다. 삭제된 등록과 로컬 후보는 다음 정상 조회에서 제외합니다. 사용자 지정 정책은 그대로 적용하며, 그 정책의 `download_hosts`에 없는 호스트로 원격 목록을 갱신하지 않습니다. 정책이 깨졌거나 누락되면 새 조회·활성화·실행은 실패합니다. 사용 중인 능력의 버전이 바뀌면 기존 접근을 해제한 뒤 새 버전을 준비합니다.

`capability_runtime_status`의 `catalog_status`에는 발견 후보 수, 정책상 허용된 후보 수, 로컬 조회 시각, 원격 갱신 시각·실패·오래된 상태가 나옵니다. 후보 수는 설치나 인증 완료 수가 아닙니다. 필요할 때 `refresh_capability_catalog`로 즉시 갱신할 수 있으며 기본 사용에는 수동 명령이 필요하지 않습니다. `read_static_skill` 자체는 알려진 로컬 파일과 기존 스냅샷을 읽고, 다운로드·프로세스 실행·파일 쓰기를 하지 않습니다.

`CAPMGR_INCLUDE_CODEX_CATALOG=0`, `CAPMGR_INCLUDE_CLAUDE_CATALOG=0`으로 해당 플랫폼 탐색을 끌 수 있습니다. `CAPMGR_SYNC_MARKETPLACES=0`은 원격 Git 갱신만 끄며 로컬 검색은 유지합니다. 명시적인 `CAPMGR_CATALOGS`나 생성자 카탈로그 인자를 사용한 격리 실행은 플랫폼 탐색을 기본으로 추가하지 않습니다. SSH Git·URL JSON·패키지 레지스트리 전체를 직접 동기화하는 기능은 포함하지 않으며, 호스트가 제공하는 native 목록의 발견 정보만 사용합니다. 새로운 계정 연결은 플랫폼의 인증 절차를 따릅니다.

[0.1.12 소스 검증](../evals/catalog-refresh-2026-10-05-0.1.12.md) 이후 양쪽 설치본을 0.1.14로 업데이트하고 이름 없는 팀 양식 요청과 일반 답변의 요약을 새 CLI 세션에서 확인했다. 기존 신뢰 훅을 켠 Codex의 기본 비대화형 승인 정책에는 불필요한 해제 시도 차단이 남아 있다. [최신 설치본 검증·정리 결과](../evals/native-deployment-2026-10-05-0.1.14.md). 참고: [OpenAI 공식 패키지·마켓플레이스 문서](https://developers.openai.com/plugins/build/plugins), [Claude 공식 마켓플레이스 문서](https://code.claude.com/docs/en/plugin-marketplaces).

## 추가 출처 등록 (선택 사항)

Claude에 등록되지 않은 사내 패키지나 ZIP을 추가할 때만 이 명령이 필요합니다. 등록 정보는 기본적으로 `~/.config/contextual-capability-manager/catalog.json`과 `policy.json`에 저장되고 Codex·Claude Code의 새 세션이 함께 읽습니다. `--config-dir` 또는 `CAPMGR_CONFIG_DIR`로 위치를 바꿀 수 있습니다. 다음 명령의 `--dry-run`은 제안 내용만 출력합니다.

```bash
python3 -m capability_manager.cli catalog-add \
  --id team-notes --name 'Team notes' \
  --description 'Prepare our team meeting notes and action items' \
  --kind skill --publisher my-team --version 1.0.0 \
  --tag meeting --tag notes --source-dir /absolute/path/to/team-notes \
  --dry-run
```

검토한 뒤 `--dry-run`을 빼고 다시 실행합니다. 승인된 원격 ZIP은 `--source-url https://.../package.zip --sha256 <64자리 해시>`로 등록할 수 있습니다. 등록 시 다운로드하지 않으며, 실제 선택된 세션에서 해시를 확인합니다. stdio MCP가 들어 있는 로컬 패키지는 해당 ID에 한해 `--allow-executable`을 명시해야 등록됩니다. 원격 패키지의 실행 필요 여부는 등록 전에 직접 확인하고 같은 옵션으로 선언해야 합니다. 읽기 도구는 `--allow-read-tool ID:도구명`, HTTP 커넥터 호스트는 `--connector-host 호스트명`으로 별도 허용합니다. 외부 쓰기와 새 OAuth 인증은 이 명령으로 허용되지 않습니다.

## 구성

- `examples/catalog.json`: 독립 실행과 평가에 쓰는 예제 목록입니다. Claude Code에서는 등록된 마켓플레이스에서 목록을 자동 구성합니다. 별도 목록에는 로컬 디렉터리 또는 SHA-256으로 고정한 HTTPS ZIP을 출처로 지정할 수 있습니다. `CAPMGR_CATALOGS`에 여러 목록 경로를 운영체제 경로 구분자로 연결할 수 있습니다.
- `examples/policy.json`: 설치 시 미리 허용한 게시자, 종류, 로컬 경로, 다운로드·커넥터 호스트, 실행 여부, 읽기·쓰기 도구 목록입니다. 모델의 추천 결과와 무관하게 코드가 이 정책을 검사합니다. 다른 파일을 쓰려면 `CAPMGR_POLICY`를 지정합니다.
- `CAPMGR_INCLUDE_CODEX_CATALOG=1`: Codex CLI의 사용 가능한 로컬 마켓플레이스 패키지를 검색 목록에 추가합니다. 해당 출처는 정책의 게시자·경로 조건을 만족해야 활성화할 수 있습니다.
- `CAPMGR_INCLUDE_CLAUDE_CATALOG=0`: Claude의 자동 마켓플레이스 검색을 끕니다. 기본값은 Claude 플러그인 실행 시 켜짐입니다. 테스트용 `CAPMGR_CLAUDE_PLUGINS_DIR`로 등록 목록의 위치를 바꿀 수 있습니다. 별도 정책 파일을 제공하면 그 정책이 자동 허용 범위보다 우선합니다.
- `CAPMGR_DATA_DIR`: 패키지 캐시와 사용 기록을 저장할 절대 경로입니다. `CAPMGR_DATA_DIR`, `PLUGIN_DATA`, `CLAUDE_PLUGIN_DATA` 순으로 유효한 경로를 사용합니다. Claude 설치 경로에서는 데이터 폴더를 유추하며, 독립 실행 기본값은 `~/.local/share/contextual-capability-manager`입니다. 훅·MCP에 같은 경로를 지정해야 합니다. 판단 기록은 기본적으로 작업 원문 대신 해시, 후보 ID·점수, 선택, 판단기, 활성화 상태만 저장합니다.
- `CAPMGR_CONFIG_DIR`: 등록한 목록·정책의 공통 위치입니다. 기본값은 `~/.config/contextual-capability-manager`입니다. 기존 플러그인 데이터 폴더의 목록·정책도 공통 설정이 없으면 읽습니다.
- `CAPMGR_STORE_DECISION_TEXT=1`: 오판 사례를 나중에 직접 검토하려고 판단에 전달한 작업 설명을 로컬 DB에 저장할 때만 켭니다. 기본값은 꺼짐이며, 켜면 최대 2,000자를 저장합니다.
- `CAPMGR_DECIDER_URL`: 선택 사항인 Jev 방식 결정 API의 `/v1/systemone` URL입니다. `CAPMGR_DECIDER_MODEL`에는 Jev·Kev·Jeff 서버가 제공하는 모델명을, `CAPMGR_DECIDER_KEY_ENV`에는 인증 토큰을 담은 환경 변수명을 넣습니다. 해당 호스트를 정책의 `decision_hosts`에도 허용해야 합니다. 토큰을 쓰는 경우 변수명을 `allowed_secret_env`에 추가합니다. 설정하지 않으면 간단한 로컬 단어 매칭을 사용합니다.

원격 결정 API를 설정하면 작업 설명과 짧은 맥락이 해당 서버로 전송됩니다. 모델의 선택 신뢰도와 필요성 확률이 각각 0.85 이상일 때만 자동 활성화합니다. 원격 결정을 설정하지 않은 경우, 로컬 판단은 추가 능력 요청이 명시되었거나 프로젝트 전용 절차·자료를 실제 작업에 써야 하고 관련 후보가 분명할 때만 활성화를 추천합니다. 단순 설명·일상적인 작업, 요청 안에 필요한 자료가 이미 제공된 작업, 후보가 모호한 작업은 보류합니다. 이 값과 규칙은 시제품의 보수적 초기값이며 실제 사용 기록으로 검증해야 합니다.
결정 API에 연결하지 못하면 후보만 보여주며 자동 설치는 진행하지 않습니다.

로컬 판단의 `confidence_kind: heuristic`과 0/1 점수는 단어 규칙의 결과이며 보정된 확률이 아닙니다. 설치·연결 거절은 원격 추천에도 우선합니다. 제공된 예시 설명과 외부 자료 조회를 구분하고, 티켓 제목만 붙여 넣은 조회 요청은 자료가 전부 제공된 것으로 간주하지 않습니다. 다중 스킬 패키지는 관련 하위 스킬 하나와 공통 루트 지침을 전달합니다. 관련 후보가 불분명하면 정적 전달을 중단합니다. 지침은 파일당 30,000바이트, 합계 60,000바이트로 제한합니다.

## 세션 요약과 정리

패키지 준비·재활성화 뒤 마지막 능력 사용을 마치고 `release_capability_session`을 호출하면 권한을 해제하고 최종 답변에 넣을 `summary_markdown`을 반환합니다. 설치와 실제 사용, 권한 종료와 캐시 삭제를 구분합니다. 다음은 검증용 세션의 표시 예입니다.

0.1.9부터 추가 능력을 사용하지 않은 요청도 답변 끝에 `추가 설치 0 · 캐시 재사용 0 · 권한 해제 0 · 활성 권한 0`을 표시하도록 안내합니다. 프롬프트 훅이 확인한 빈 세션 기록을 전달하므로, 이후 준비·재활성화 호출이 없으면 0개 표시를 위해 관리자 도구를 호출하거나 추가 승인을 받을 필요가 없습니다. 준비를 시도했다면 마지막 사용 뒤 release의 최신 요약을 사용합니다. 기록 조회에 실패하면 0개로 추정하지 않고 확인 불가로 표시합니다. 이 수치는 관리자 기록 범위이며 컴퓨터에 설치된 모든 네이티브 스킬·플러그인의 개수가 아닙니다.

0.1.10의 포함 스킬은 훅이 없어도 완전한 채팅 기록에서 준비·재활성화 시도와 이전 활성 권한·정리 오류가 없음을 확인할 수 있으면 0개 표시를 안내합니다. 이력이나 정리가 불확실하면 실제 세션 기록을 조회하거나 확인 불가로 표시합니다. `read_static_skill`만 호출한 경우에는 그 조회 요약을 표시하며, 결과 기록·release를 새로 호출하지 않습니다. 로컬 지침 읽기는 설치 0개, 기존 캐시 읽기는 캐시 재사용 1개로 구분합니다. 조회 요약은 이전 준비나 남아 있는 권한을 해제하지 않으며 이전 세션 합계를 대신하지 않습니다. 이 경로는 DB에 판단·사용 이벤트를 쓰지 않으므로 기존 판단 보고서의 표본에 포함되지 않습니다.

| 능력 | 종류 | 준비 | 사용 결과 | 세션 권한 | 캐시 |
| --- | --- | --- | --- | --- | --- |
| 회의록 지침 | 스킬 | 새로 설치 | 지침 전달 · 결과 미확인 | 해제 완료 | 보관 |
| 노트 조회 | 플러그인 | 캐시 재사용 | 도구 호출 1회 | 해제 완료 | 보관 |

`capability_session_summary` 또는 CLI `session-report --session <ID> [--format json]`으로 같은 기록을 조회합니다. 새 세션은 캐시 재사용으로 표시하고, 같은 세션의 반복 활성화는 한 행으로 집계합니다. 실제 도구 호출과 에이전트가 보고한 성공·실패는 구분하며 정적 지침 전달만으로 성공을 추정하지 않습니다. 설치·연결 실패도 사용 불가나 검토 필요로 남습니다. 사용 가능한 지침이나 승인된 도구가 없으면 `unavailable`을 반환하고 해당 권한을 취소합니다. 일부만 준비되면 `partially_activated`로 반환합니다.

요약은 `<DATA_DIR>/session-summaries/<세션 ID의 해시>/summary.json`과 `summary.md`에 저장합니다. 패키지 이름·종류·버전과 준비 기록을 새 테이블에 보관하므로 카탈로그가 사라져도 조회할 수 있습니다. 이전 버전의 활성 기록은 출처를 추정하지 않고 `이전 기록 없음`으로 표시합니다. 네이티브 플러그인 관리자를 통해 따로 설치한 항목은 관찰 범위에 포함되지 않습니다.

SessionEnd 훅은 카탈로그 탐색이나 다운로드 없이 권한을 해제하고 같은 보고서를 저장합니다. 최종 답변 표시는 스킬 안내와 명시적 release 호출을 통해 이루어집니다. Codex의 SessionEnd는 보관·삭제·앱 종료·장시간 비활성 등에서 실행되고 단순 채팅 전환에는 실행되지 않습니다. 종료 출력은 최종 답변을 추가하는 수단이 아니며 Claude도 해당 훅의 출력을 무시합니다. [Codex 훅 문서](https://learn.chatgpt.com/docs/hooks), [Claude 훅 문서](https://code.claude.com/docs/en/hooks).

시작·프롬프트 훅이 전달하는 `storage_id`를 MCP 호출의 `expected_storage_id`로 넘기면 저장소 불일치 시 설치·호출을 거부합니다. CLI에서는 전역 옵션 `--expected-storage-id`를 사용합니다. `capability_runtime_status` 또는 `status --session <ID>`는 실제 저장소 ID, 플랫폼, 버전, 세션 연결 여부, 최근 훅 오류를 반환합니다. 오류 로그 `hook-errors.jsonl`에는 오류 종류와 이벤트 메타데이터만 남고 요청 원문·예외 메시지·작업 경로는 없습니다. Codex의 Claude 호환 환경 변수가 있어도 Codex 플랫폼으로 기록합니다.

## 판단 검증

`resolve_capability`은 선택한 후보뿐 아니라 **선택하지 않은 판단**도 기록하고 `decision_id`를 반환합니다. `search_capabilities`는 `session_id`를 함께 주면 판단을 기록합니다. 기록에는 판단기·모델, 후보 점수, 선택 결과, 필요성 확률·선택 신뢰도, 활성화 성공 여부가 포함됩니다.

0.1.7부터 Codex·Claude 모두 같은 `capability_events` 형식으로 요청 관찰, 탐색, 지침 전달, 게이트웨이 도구 호출, 명시적 사용 결과, 정답 라벨, 세션 해제를 기록합니다. 각 이벤트에는 플랫폼·플러그인 버전·세션/판단 ID·상태가 있고 요청 원문과 도구 인수는 없습니다. 기존 DB에 테이블을 추가하며 이전 사용을 이벤트로 추정해 채우지는 않습니다. 두 플랫폼의 DB는 별개입니다. `python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> event-report --days 30`으로 집계를, `events --session <세션 ID>`로 경로를 확인합니다. 관리자를 거치지 않은 네이티브 플러그인 사용이나 최종 답변의 실제 품질은 자동으로 관찰하지 않습니다.

0.1.8의 집계는 이벤트 수 외에 전체·이벤트별 고유 세션 수, 기록된 단계 지연의 표본 수와 평균을 반환합니다. 반복 호출을 사용자 수로 해석하지 않습니다. `other` 정답의 보류는 올바른 후보 선택으로 세며, 목록에 있는 능력의 누락과 분리합니다. 필요성 판단 정밀도·재현율은 후보 선택 정확도와 별도로 집계합니다. 이 수치만으로 답변 품질이나 시간 절감 효과를 입증할 수는 없습니다.

UserPromptSubmit 훅은 매 사용자 요청마다 **로컬 단어 매칭만** 실행해, 에이전트가 탐색을 건너뛴 경우까지 `prompt-observer` 판단으로 기록합니다. 요청 원문은 저장하거나 원격 결정 API로 보내지 않고 해시만 기록합니다. 명시적인 추가 능력 요청 또는 프로젝트 전용 작업에 분명한 후보가 있는 경우에만 짧은 후보 힌트를 모델에 전달합니다. 탐색 도구가 호출되면 같은 `turn_id`의 관찰 상태를 `searched`로 바꿉니다. 판단 보고서의 `prompt_observations`는 전체·탐색·미탐색 건수와 명시적으로 라벨링된 누락 건수를 보여줍니다. 라벨이 없는 미탐색 건수는 오판으로 간주하지 않습니다.

에이전트가 원래 요청을 짧게 바꿔 도구에 전달해도, 같은 세션·턴의 로컬 관찰에서 이미 분명한 후보를 찾았다면 이를 재사용합니다. 후보가 여전히 작업 설명과 관련 있고 정책상 허용될 때에만 `prompt-observer-assisted`로 기록합니다. 명시적인 거절이나 요청에 자료가 이미 제공된 경우에는 재사용하지 않습니다.

사용 성공 여부와 **후보 선택의 정답**은 별도로 기록합니다. 사용자가 판단을 확인하거나 교정했을 때만 `record_decision_feedback`으로 정답을 붙입니다. 정답은 카탈로그 ID, `none`(새 능력 불필요), `other`(필요했지만 목록에 없음) 중 하나입니다. `list_capability_decisions`에서 미평가 판단을 보고 `capability_decision_report`에서 판단기별 선택률, 평가된 표본 수, 정확도, 잘못된 선택·누락 수를 조회합니다. 정답이 없는 판단은 정확도 분모에 넣지 않습니다.

CLI에서는 `decisions`, `feedback <decision_id> <정답> --session <session_id>`, `report` 명령을 사용합니다. 기본 설정은 작업 설명 원문을 저장하지 않으므로 사후 판정에는 당시 채팅 맥락이 필요합니다. 정확도는 명시적으로 평가된 표본에 한정되며, 사용 성공 기록만으로 자동 정답을 만들지 않습니다.
`record_capability_result`에는 회의록이나 작업 설명을 다시 보내지 않습니다. 서버가 세션에 저장된 프로젝트 키 또는 직전 판단의 맥락 키를 사용해 성공 여부를 기록합니다.

`evals/cases.json`과 `evals/challenge.json`에는 합성 요청과 사람이 붙인 정답이 있습니다. `python3 -m capability_manager.evaluation`은 설치 없이 현재 로컬 판단기의 필요성 판정과 후보 선택을 평가합니다. `--cases`로 추가 사례집을 고르고 `--output`으로 JSON 결과를 저장할 수 있습니다. `--backend configured`는 `CAPMGR_DECIDER_URL`을 명시적으로 설정했을 때만 사용하며, 합성 요청을 해당 결정 서버로 전송합니다. 이 평가는 에이전트가 실제로 탐색 도구를 호출했는지, 설치가 성공했는지, 결과가 유용했는지는 측정하지 않습니다. 새 채팅과 실제 라벨 검증 절차는 [evals/README.md](../evals/README.md)에 정리했습니다.

Jev·Kev는 `python3 -m capability_manager.shadow --cases evals/cases.json`으로 먼저 로컬 기준선과 실행 계획만 확인합니다. 검토한 사례집과 승인 정책에 결정 서버를 설정한 뒤 `--allow-remote`를 붙여야 두 모델에 사례를 전송합니다. 비교 실행은 활성화나 설치를 바꾸지 않으며 모델별 정확도·누락·오선택과 총 지연 시간을 보고합니다. 서버가 실패해 로컬 fallback이 생긴 모델은 유효한 비교 결과로 표시하지 않습니다. 응답에서 비용 정보는 제공받지 않아 비용은 별도로 확인해야 합니다. 실사용 표본이 충분히 검토되기 전까지 이 비교만으로 자동 라우팅을 켜지 않습니다.

## 현재 범위

지침형 스킬과 HTTP·stdio MCP 서버를 지원합니다. 설치한 패키지의 스킬 지침은 MCP 도구 결과로 즉시 전달되고, MCP 도구는 관리자 게이트웨이에서 바로 호출할 수 있습니다. 이는 실행 중인 Codex나 Claude의 **네이티브 플러그인 등록 목록을 변경하는 기능은 아닙니다**. 앱 UI, 다운로드된 플러그인의 훅, 임의의 OAuth 계정 연결은 아직 지원하지 않습니다. 커넥터는 승인된 서버와 이미 제공된 bearer 토큰을 사용할 수 있으며, 새 인증이 필요하면 오류를 반환합니다.

읽기 도구는 정책의 이름 허용 목록과 MCP 도구의 `readOnlyHint: true`가 모두 있어야 호출됩니다. 쓰기 도구는 별도 쓰기 허용 목록과 `allow_external_write`가 필요합니다. 도구 제공자의 주석 자체는 신뢰 증명이 아니므로 실제 외부 변경에는 별도의 사용자 승인 정책을 유지해야 합니다.

세션 종료 훅은 세션 사용 권한을 해제하고 다운로드 캐시는 보존합니다. 이는 Claude의 네이티브 플러그인을 설치하거나 제거하는 동작이 아닙니다. `installed_plugins.json`에는 관리자 플러그인만 등록되며, `~/.claude/plugins/cache`에 남은 다른 패키지 폴더는 Claude가 관리하는 캐시일 수 있습니다. 명시적 release는 게이트웨이 연결을 닫으며 종료 오류가 나도 권한을 해제하고 오류를 보고서에 남깁니다. 다른 프로세스로 실행되는 종료 훅은 권한을 취소하고 게이트웨이 연결은 그 프로세스가 종료될 때 닫힙니다. 훅 실행 전 신뢰 검토가 필요하며, 갑작스러운 프로세스 종료에 대비해 활성 기록에는 24시간 만료 시간이 있습니다. 같은 맥락에서 서로 다른 세션 세 번이 30일 내 성공하면 `prefetch <맥락>`으로 패키지를 미리 준비할 수 있습니다. 훅 제한 시간 안에 정리를 마치도록 시작·종료 훅에서는 다운로드하지 않습니다.
세션 시작 훅은 프로젝트 작업 경로의 **해시**를 세션 ID에 연결합니다. 모델이 도구에 전달하는 짧은 작업 맥락은 후보 선택에 쓰고, 예열·사용 기록에는 훅의 안정적인 프로젝트 키를 사용합니다. 훅이 실행되지 않으면 도구 인자의 맥락을 대신 쓰며 결과에 `context_source: argument`로 표시됩니다.
