# 판단 검증 절차

## 1. 로컬 판단 기준선

```bash
python3 -m capability_manager.evaluation --output /private/tmp/capability-eval.json
python3 -m capability_manager.evaluation --cases evals/challenge.json \
  --output /private/tmp/capability-challenge.json
python3 -m capability_manager.evaluation --cases evals/boundaries.json \
  --output /private/tmp/capability-boundaries.json
python3 -m unittest discover -s tests -v
```

`cases.json`은 직접·간접·부정·목록에 없는 능력 요청을, `challenge.json`은 추가 간접·부정 사례를 다룹니다. 각 사례의 `expected`는 사용할 카탈로그 ID, `none`(추가 능력 불필요), `other`(필요하지만 목록에 없음) 중 하나입니다. `available_ids`는 해당 사례에서 볼 수 있는 후보를 제한합니다. 사례를 수정하기 전에 현재 결과를 별도로 저장하고, 오판 사례를 추가할 때는 정답 이유를 검토하세요. 합성 사례에서 높은 점수는 실사용 정확도의 증거가 아닙니다.

`boundaries.json`은 2026-10-03 분석에서 발견한 설치·연결 거절, 설명만 하는 작업, 제공된 자료의 범위, 소유 표현 없는 외부 조회를 검증하는 12건의 합성 회귀 사례입니다. 이 집합은 개선을 유도한 사례이므로 독립적인 실사용 검증 집합으로 취급하지 않습니다. 결과는 [current-boundaries-lexical.json](current-boundaries-lexical.json)에 저장합니다.

초기 `0.1.0`의 16건 기준선은 [baseline-lexical.json](baseline-lexical.json)에, 현재 결과는 [current-lexical.json](current-lexical.json)과 [current-challenge-lexical.json](current-challenge-lexical.json)에 저장했습니다. 새 CLI 세션에서 관찰한 실패·수정 과정은 [live-cli-2026-09-30.md](live-cli-2026-09-30.md)에 있습니다.
0.1.4 설치 후 읽기 전용 CLI 검증과 승인 정책에 따른 한계는 [live-cli-2026-09-30-0.1.4.md](live-cli-2026-09-30-0.1.4.md)에 있습니다.

0.1.8의 설치된 Claude Code 플러그인을 새 CLI 세션에서 확인한 결과는 [live-claude-2026-10-04-0.1.8.md](live-claude-2026-10-04-0.1.8.md)에 있습니다. 재로그인 후 해당 실행에 검증 도구를 좁게 허용한 조건에서 스킬 적용·사용 결과 기록·권한 해제·최종 답변 요약표까지 확인했습니다. 기본 도구 승인까지 자동 처리된다는 증거는 아닙니다.

0.1.9의 자동 0개 영수증과 기존 스킬 사용 흐름의 실제 설치본 결과는 [live-claude-2026-10-04-0.1.9.md](live-claude-2026-10-04-0.1.9.md)에 있습니다. 빈 요청은 추가 도구 호출 없이 표시됐고, 스킬 사용은 해당 실행에만 좁게 도구를 허용해 확인했습니다. 검증 중 MCP 연결 실패와 실패 캐시, 공식 SDK 재연결 후 새 세션 복구도 기록했습니다.

Codex의 훅 신뢰 자동화 지원 범위와 설치만으로 동작하기 위한 구조 개선안은 [codex-installation-friction-2026-10-04.md](codex-installation-friction-2026-10-04.md)에 있습니다. 훅을 핵심 경로에서 분리하는 개선안은 아직 구현되지 않았습니다.

보고서의 `need_precision`과 `need_recall`은 능력 필요 여부를, `known_capability_accuracy`는 카탈로그에 있는 정답 능력을 실제로 선택했는지를 봅니다. `false_activations`는 `none` 사례에서 선택한 횟수, `missed_capabilities`는 존재하는 정답 후보를 놓친 횟수입니다. `other` 사례는 필요성 감지와 잘못된 자동 선택을 별도 집계합니다. 판단 임곗값은 사례와 실제 라벨이 쌓인 뒤 조정합니다.

## 2. 새 채팅에서 전체 흐름 확인

반복 가능한 Codex CLI 실환경 검사는 다음처럼 실행합니다. 각 실행은 새 임시 작업 폴더와 독립된 판단 DB를 사용하며, 합성 요청의 CLI 이벤트와 요약 결과를 `evals/runs/`에 저장합니다. `--repeat`를 늘리면 같은 요청의 탐색 일관성을 볼 수 있습니다.

```bash
python3 scripts/run_live_codex_eval.py --repeat 2
```

`report.json`은 훅 관찰, 에이전트의 관리자 도구 호출 시도, 승인 거부, 탐색, 활성화를 따로 집계합니다. 훅이 관찰되지 않은 실행은 모델의 탐색 실패로 계산하지 않습니다. 비대화형 CLI의 기본 승인 정책은 MCP 호출을 거부할 수 있으므로, `manager_tool_approval_denials`가 있으면 활성화 실패를 모델의 판단 실패로 해석하지 않습니다. 격리된 테스트 DB 환경 변수는 Codex가 MCP 서버로 전달하지 않을 수 있습니다. `unbound_manager_searches`가 있으면 훅과 MCP 서버가 서로 다른 상태 DB를 사용했으므로 같은 턴 판단 재사용과 활성 권한 해제를 이 실행으로 검증할 수 없습니다. 긍정 사례의 활성화 여부는 사용 성공이나 답변 품질을 뜻하지 않으므로 이벤트 로그와 최종 답변도 확인해야 합니다. 이 스크립트는 Codex 전용이며 Claude Code는 별도로 검증합니다.

플러그인 패키지가 바뀌면 새 채팅에서 아래 요청을 각각 시험하고 도구 호출, 설치 결과, 최종 답변을 확인합니다. 현재 기본 카탈로그에는 `note-summarizer`만 있으므로 다른 능력의 실제 설치 검증에는 승인된 시험용 카탈로그가 필요합니다.

| 요청 | 기대 동작 |
| --- | --- |
| `회의록을 정리하는 스킬을 써줘` | 탐색 → `note-summarizer` 선택 → 지침 적용 |
| `이 메모를 우리 팀 회의록 양식에 맞춰 결정사항과 담당자까지 정리해줘` | 이름을 말하지 않아도 탐색 → 선택 → 지침 적용 |
| `이 두 문장을 한 문장으로 요약해줘` | 추가 능력 설치 없음 |
| `아래에 붙인 양식대로 이 메모를 정리해줘` | 제공된 양식을 사용하고 추가 설치 없음 |
| `현재 티켓 담당자를 조회해줘` | 기본 목록에 없으므로 임의의 능력 설치 없음 |

각 시험에서 SessionStart의 프로젝트 키 연결, UserPromptSubmit의 관찰 기록, MCP 탐색·활성화, 사용 결과, 마지막 능력 사용 후 release의 요약, SessionEnd의 권한 해제를 확인합니다. 훅의 `storage_id`를 `expected_storage_id`로 전달해 같은 저장소인지 확인합니다. `record_capability_result`의 성공 값은 실제 작업 결과를 확인한 뒤에만 기록합니다. 플러그인·커넥터 시험은 읽기 도구의 주석과 정책 허용 목록, 인증 실패, 쓰기 거부까지 확인합니다. `unavailable`과 `partially_activated`를 정상 전달과 구분하고 캐시가 있다는 이유만으로 활성화를 성공 처리하지 않습니다.

## 3. 실사용 라벨과 수정

```bash
python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> decisions --days 30
python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> feedback <decision_id> <ID|none|other> --session <session_id>
python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> report --days 30
python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> event-report --days 30
python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> events --session <session_id>
python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> status --session <session_id>
python3 -m capability_manager.cli --data-dir <PLUGIN_DATA> session-report --session <session_id>
```

선택된 판단과 `not_searched`를 모두 표본 추출해 원래 채팅을 보며 라벨을 붙입니다. 기본 기록은 요청 원문을 저장하지 않으므로 나중에 해시만으로 정답을 복원할 수 없습니다. 모든 자동 선택·실패는 검토하고, 미탐색 건도 무작위로 뽑아야 누락률의 편향을 줄일 수 있습니다. 설치 성공과 정답 라벨은 별개입니다.

Codex와 Claude의 `PLUGIN_DATA`는 별개이므로 두 DB를 각각 조회합니다. 0.1.7 이후의 `capability_events`에는 `platform`과 `plugin_version`이 들어갑니다. `capability_delivered`는 지침이나 게이트웨이가 전달됐다는 뜻이며 실제 작업 성공의 증거가 아닙니다. `tool_call`에는 도구 이름·상태·지연 시간만 기록하고 인수와 응답은 저장하지 않습니다. 직접 설치한 네이티브 플러그인의 사용은 이 DB에서 알 수 없습니다.

실제 효용을 평가하려면 같은 유형의 작업을 관리자 사용·미사용 조건으로 비교해 성공률, 소요 시간, 수정 횟수를 별도로 기록해야 합니다. 현재 단계 지연은 탐색·전달·호출의 일부 시간이며 전체 작업 시간이나 절감 시간의 대체 지표가 아닙니다. 고유 세션 수와 명시적 정답 라벨 수를 함께 보고, 같은 세션의 반복 호출을 독립 표본으로 세지 않습니다. 설치 기록·사용 결과·정답 라벨·최종 답변 품질은 각각 다른 증거입니다.

## 4. Jev·Kev 비교 실행

실제 채팅을 검토한 뒤 개인정보와 내부 내용을 제거한 사례만 `evals/real-reviewed.local.json`에 수동으로 작성합니다. 이 파일은 Git에서 제외됩니다. `evals/cases.json` 형식으로 `capabilities`에 당시 비교할 후보의 메타데이터를, 각 `cases`에 익명화한 `prompt`, `expected`(`ID`, `none`, `other`), `available_ids`를 넣습니다. 전체 후보 집합을 공정하게 반영하고, 정답 후보를 빠뜨리지 않도록 확인합니다. 원문이 없는 기존 로그에서 요청을 임의로 재구성하지 않습니다.

```bash
python3 -m capability_manager.shadow --cases evals/real-reviewed.local.json
CAPMGR_DECIDER_URL=https://<승인된 호스트>/v1/systemone \
  python3 -m capability_manager.shadow \
  --cases evals/real-reviewed.local.json --policy <승인 정책 JSON> \
  --models jev-latest kev-latest --allow-remote \
  --output /private/tmp/capability-shadow.json
```

첫 명령은 원격 요청 없이 기준선만 출력합니다. 두 번째 명령은 사례의 `prompt`와 후보 메타데이터를 설정된 서버에 보냅니다. 정책의 `decision_hosts`가 호스트를 허용해야 하고 인증 토큰을 쓰면 `allowed_secret_env`도 설정해야 합니다. 결과의 `status=evaluated`만 모델 비교에 사용합니다. `backend_unavailable`은 timeout·잘못된 응답·fallback이 포함된 결과입니다. 비용 사용량은 현재 결정 API 응답에서 받지 못합니다. 충분한 실사용 라벨에서 잘못된 자동 선택, 누락, 지연 시간, 실제 비용을 비교한 뒤에만 실사용 모델을 수동 설정합니다. 이 도구는 설치 정책이나 실사용 라우팅을 변경하지 않습니다.

개선할 때는 오판을 필요성 판단, 후보 선택, 정책·인증, 설치·호출, 답변 품질로 나누고 한 단계씩 수정합니다. 새 사례를 평가집에 추가한 뒤 두 집합과 단위·통합 테스트를 다시 실행합니다. 실제 라벨의 표본 수와 오류 유형이 충분히 쌓이기 전에는 합성 사례 점수만으로 자동 활성화 범위를 넓히지 않습니다.
