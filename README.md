# Contextual Capability Manager

미설치 스킬·MCP 플러그인·커넥터를 작업 도중 찾아 승인된 범위에서 설치하고, **같은 세션에서** 지침을 읽거나 도구를 호출하는 시제품입니다. Python 표준 라이브러리만 사용합니다.

## 실행

```bash
python3 -m unittest discover -s tests -v
python3 -m capability_manager.evaluation
python3 -m capability_manager.evaluation --cases evals/challenge.json
python3 -m capability_manager.cli --data-dir /private/tmp/capability-manager-demo \
  resolve 'summarize meeting notes' --session demo-1 --context sample-project
python3 -m capability_manager.cli --data-dir /private/tmp/capability-manager-demo \
  release --session demo-1
```

로컬 Codex 마켓플레이스 패키지를 만들려면 `python3 scripts/build_marketplace.py`를 실행합니다. 생성된 `dist/marketplace`에는 설치 가능한 플러그인과 마켓플레이스 목록이 들어 있습니다. 검토 후 `codex plugin marketplace add <절대 경로의 dist/marketplace>`와 `codex plugin add contextual-capability-manager@local-capabilities`로 설치할 수 있습니다. 설치 후 새 채팅을 시작하고 `/hooks`에서 SessionStart, UserPromptSubmit, SessionEnd를 각각 검토·신뢰해야 합니다. 관리자 플러그인은 이후 작업 중 필요한 *다른* 능력을 MCP 게이트웨이를 통해 같은 채팅에 적용합니다.

현재 시험 환경의 Codex CLI `0.158.0-alpha.2`에서는 패키지에 포함된 훅이 `/hooks`에 표시되지 않았습니다. 이 환경에서는 신뢰한 훅 명령을 `~/.codex/hooks.json`에 사용자 훅으로 등록하고, 플러그인의 `PLUGIN_DATA` 경로를 동일하게 지정해 사용합니다. 이 설정은 플러그인 설치만으로 자동 생성되지 않습니다. 훅의 모델용 안내에는 작업 경로와 사용자 요청 원문을 포함하지 않습니다.

Claude Code용 `.claude-plugin/plugin.json`, `.mcp.json`, 세션 훅도 포함합니다. 로컬 검증은 `claude --plugin-dir <이 저장소의 절대 경로>`로 시작할 수 있습니다. 사용자 범위 설치는 비공개 저장소에 접근 가능한 계정에서 `claude plugin marketplace add starhn87/contextual-capability-manager`, `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1 claude plugin install contextual-capability-manager@contextual-capabilities`를 사용합니다. 새 세션에서 `/mcp`와 `/plugin`으로 로딩 상태를 확인하세요. Claude 훅에는 Codex의 `turn_id`가 없으므로 제출 때마다 ID를 생성해 판단 기록과 도구 호출을 연결합니다.

Claude Code에서는 별도 목록 등록 없이 **이미 등록된 Claude 마켓플레이스**를 매 세션 검색합니다. 미설치 로컬 지침형 플러그인과 커밋 SHA가 고정된 HTTPS Git 플러그인을 후보로 읽고, 실제 필요할 때만 정적 스킬 파일을 세션 캐시에 복사해 같은 대화에 전달합니다. Git 후보에 MCP 서버나 훅이 있으면 자동 실행하지 않고 검토 대상으로 남깁니다. 이미 설치된 플러그인은 Claude가 직접 사용할 수 있으므로 중복 후보에서 제외합니다. 이 검색은 Claude 마켓플레이스 등록 목록을 사용하며, 마켓플레이스 자체를 새로 구독하거나 OAuth 계정을 연결하지는 않습니다. 판단 결과는 로컬에 기록합니다.

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
- `CAPMGR_DATA_DIR`: 패키지 캐시와 사용 기록을 저장할 폴더입니다. Codex에서는 `PLUGIN_DATA`, Claude Code에서는 `CLAUDE_PLUGIN_DATA`가 기본값입니다. 판단 기록은 기본적으로 작업 원문 대신 해시, 후보 ID·점수, 선택, 판단기, 활성화 상태만 저장합니다.
- `CAPMGR_CONFIG_DIR`: 등록한 목록·정책의 공통 위치입니다. 기본값은 `~/.config/contextual-capability-manager`입니다. 기존 플러그인 데이터 폴더의 목록·정책도 공통 설정이 없으면 읽습니다.
- `CAPMGR_STORE_DECISION_TEXT=1`: 오판 사례를 나중에 직접 검토하려고 판단에 전달한 작업 설명을 로컬 DB에 저장할 때만 켭니다. 기본값은 꺼짐이며, 켜면 최대 2,000자를 저장합니다.
- `CAPMGR_DECIDER_URL`: 선택 사항인 Jev 방식 결정 API의 `/v1/systemone` URL입니다. `CAPMGR_DECIDER_MODEL`에는 Jev·Kev·Jeff 서버가 제공하는 모델명을, `CAPMGR_DECIDER_KEY_ENV`에는 인증 토큰을 담은 환경 변수명을 넣습니다. 해당 호스트를 정책의 `decision_hosts`에도 허용해야 합니다. 토큰을 쓰는 경우 변수명을 `allowed_secret_env`에 추가합니다. 설정하지 않으면 간단한 로컬 단어 매칭을 사용합니다.

원격 결정 API를 설정하면 작업 설명과 짧은 맥락이 해당 서버로 전송됩니다. 모델의 선택 신뢰도와 필요성 확률이 각각 0.85 이상일 때만 자동 활성화합니다. 원격 결정을 설정하지 않은 경우, 로컬 판단은 추가 능력 요청이 명시되었거나 프로젝트 전용 절차·자료를 실제 작업에 써야 하고 관련 후보가 분명할 때만 활성화를 추천합니다. 단순 설명·일상적인 작업, 요청 안에 필요한 자료가 이미 제공된 작업, 후보가 모호한 작업은 보류합니다. 이 값과 규칙은 시제품의 보수적 초기값이며 실제 사용 기록으로 검증해야 합니다.
결정 API에 연결하지 못하면 후보만 보여주며 자동 설치는 진행하지 않습니다.

## 판단 검증

`resolve_capability`은 선택한 후보뿐 아니라 **선택하지 않은 판단**도 기록하고 `decision_id`를 반환합니다. `search_capabilities`는 `session_id`를 함께 주면 판단을 기록합니다. 기록에는 판단기·모델, 후보 점수, 선택 결과, 필요성 확률·선택 신뢰도, 활성화 성공 여부가 포함됩니다.

0.1.7부터 Codex·Claude 모두 같은 `capability_events` 형식으로 요청 관찰, 탐색, 지침 전달, 게이트웨이 도구 호출, 명시적 사용 결과, 정답 라벨, 세션 해제를 기록합니다. 각 이벤트에는 플랫폼·플러그인 버전·세션/판단 ID·상태가 있고 요청 원문과 도구 인수는 없습니다. 기존 DB에 테이블을 추가하며 이전 사용을 이벤트로 추정해 채우지는 않습니다. 두 플랫폼의 DB는 별개입니다. `python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> event-report --days 30`으로 집계를, `events --session <세션 ID>`로 경로를 확인합니다. 관리자를 거치지 않은 네이티브 플러그인 사용이나 최종 답변의 실제 품질은 자동으로 관찰하지 않습니다.

UserPromptSubmit 훅은 매 사용자 요청마다 **로컬 단어 매칭만** 실행해, 에이전트가 탐색을 건너뛴 경우까지 `prompt-observer` 판단으로 기록합니다. 요청 원문은 저장하거나 원격 결정 API로 보내지 않고 해시만 기록합니다. 명시적인 추가 능력 요청 또는 프로젝트 전용 작업에 분명한 후보가 있는 경우에만 짧은 후보 힌트를 모델에 전달합니다. 탐색 도구가 호출되면 같은 `turn_id`의 관찰 상태를 `searched`로 바꿉니다. 판단 보고서의 `prompt_observations`는 전체·탐색·미탐색 건수와 명시적으로 라벨링된 누락 건수를 보여줍니다. 라벨이 없는 미탐색 건수는 오판으로 간주하지 않습니다.

에이전트가 원래 요청을 짧게 바꿔 도구에 전달해도, 같은 세션·턴의 로컬 관찰에서 이미 분명한 후보를 찾았다면 이를 재사용합니다. 후보가 여전히 작업 설명과 관련 있고 정책상 허용될 때에만 `prompt-observer-assisted`로 기록합니다. 명시적인 거절이나 요청에 자료가 이미 제공된 경우에는 재사용하지 않습니다.

사용 성공 여부와 **후보 선택의 정답**은 별도로 기록합니다. 사용자가 판단을 확인하거나 교정했을 때만 `record_decision_feedback`으로 정답을 붙입니다. 정답은 카탈로그 ID, `none`(새 능력 불필요), `other`(필요했지만 목록에 없음) 중 하나입니다. `list_capability_decisions`에서 미평가 판단을 보고 `capability_decision_report`에서 판단기별 선택률, 평가된 표본 수, 정확도, 잘못된 선택·누락 수를 조회합니다. 정답이 없는 판단은 정확도 분모에 넣지 않습니다.

CLI에서는 `decisions`, `feedback <decision_id> <정답> --session <session_id>`, `report` 명령을 사용합니다. 기본 설정은 작업 설명 원문을 저장하지 않으므로 사후 판정에는 당시 채팅 맥락이 필요합니다. 정확도는 명시적으로 평가된 표본에 한정되며, 사용 성공 기록만으로 자동 정답을 만들지 않습니다.
`record_capability_result`에는 회의록이나 작업 설명을 다시 보내지 않습니다. 서버가 세션에 저장된 프로젝트 키 또는 직전 판단의 맥락 키를 사용해 성공 여부를 기록합니다.

`evals/cases.json`과 `evals/challenge.json`에는 합성 요청과 사람이 붙인 정답이 있습니다. `python3 -m capability_manager.evaluation`은 설치 없이 현재 로컬 판단기의 필요성 판정과 후보 선택을 평가합니다. `--cases`로 추가 사례집을 고르고 `--output`으로 JSON 결과를 저장할 수 있습니다. `--backend configured`는 `CAPMGR_DECIDER_URL`을 명시적으로 설정했을 때만 사용하며, 합성 요청을 해당 결정 서버로 전송합니다. 이 평가는 에이전트가 실제로 탐색 도구를 호출했는지, 설치가 성공했는지, 결과가 유용했는지는 측정하지 않습니다. 새 채팅과 실제 라벨 검증 절차는 [evals/README.md](evals/README.md)에 정리했습니다.

Jev·Kev는 `python3 -m capability_manager.shadow --cases evals/cases.json`으로 먼저 로컬 기준선과 실행 계획만 확인합니다. 검토한 사례집과 승인 정책에 결정 서버를 설정한 뒤 `--allow-remote`를 붙여야 두 모델에 사례를 전송합니다. 비교 실행은 활성화나 설치를 바꾸지 않으며 모델별 정확도·누락·오선택과 총 지연 시간을 보고합니다. 서버가 실패해 로컬 fallback이 생긴 모델은 유효한 비교 결과로 표시하지 않습니다. 응답에서 비용 정보는 제공받지 않아 비용은 별도로 확인해야 합니다. 실사용 표본이 충분히 검토되기 전까지 이 비교만으로 자동 라우팅을 켜지 않습니다.

## 현재 범위

지침형 스킬과 HTTP·stdio MCP 서버를 지원합니다. 설치한 패키지의 스킬 지침은 MCP 도구 결과로 즉시 전달되고, MCP 도구는 관리자 게이트웨이에서 바로 호출할 수 있습니다. 이는 실행 중인 Codex나 Claude의 **네이티브 플러그인 등록 목록을 변경하는 기능은 아닙니다**. 앱 UI, 다운로드된 플러그인의 훅, 임의의 OAuth 계정 연결은 아직 지원하지 않습니다. 커넥터는 승인된 서버와 이미 제공된 bearer 토큰을 사용할 수 있으며, 새 인증이 필요하면 오류를 반환합니다.

읽기 도구는 정책의 이름 허용 목록과 MCP 도구의 `readOnlyHint: true`가 모두 있어야 호출됩니다. 쓰기 도구는 별도 쓰기 허용 목록과 `allow_external_write`가 필요합니다. 도구 제공자의 주석 자체는 신뢰 증명이 아니므로 실제 외부 변경에는 별도의 사용자 승인 정책을 유지해야 합니다.

세션 종료 훅은 세션 사용 권한을 해제하고 다운로드 캐시는 보존합니다. 이는 Claude의 네이티브 플러그인을 설치하거나 제거하는 동작이 아닙니다. `installed_plugins.json`에는 관리자 플러그인만 등록되며, `~/.claude/plugins/cache`에 남은 다른 패키지 폴더는 Claude가 관리하는 캐시일 수 있습니다. 게이트웨이 프로세스에 남은 MCP 연결은 그 프로세스가 종료될 때 닫힙니다. 훅 실행 전 신뢰 검토가 필요하며, 갑작스러운 프로세스 종료에 대비해 활성 기록에는 24시간 만료 시간이 있습니다. 같은 맥락에서 서로 다른 세션 세 번이 30일 내 성공하면 세션 시작 시 해당 패키지를 미리 준비합니다.
세션 시작 훅은 프로젝트 작업 경로의 **해시**를 세션 ID에 연결합니다. 모델이 도구에 전달하는 짧은 작업 맥락은 후보 선택에 쓰고, 예열·사용 기록에는 훅의 안정적인 프로젝트 키를 사용합니다. 훅이 실행되지 않으면 도구 인자의 맥락을 대신 쓰며 결과에 `context_source: argument`로 표시됩니다.
