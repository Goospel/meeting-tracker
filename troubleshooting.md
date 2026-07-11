# troubleshooting.md — 함정 기록장

> **이 문서의 역할**: 작업 중 만난 함정(Trap)을 기록하고, **반복되면 재발 자체를 막는 층으로 승격**한다.
> 목표는 "같은 삽질 두 번 안 하기".
>
> - 앞으로 할 일은 [`plan.md`](plan.md), 완료 기록은 [`changeLog.md`](changeLog.md).
> - 여긴 **"이렇게 하지 마라 / 이 증상엔 이 해결"**(Trap). *"왜 이렇게 동작하는가"*(개념 이해)는 학습 노트로.

## T-### 번호 규칙과 승격 층

각 함정은 `T-###` ID를 갖는다. 번호는 사용자의 **글로벌 시퀀스를 잇는다**(마지막 알려진 값 = `T-026`, 이후 이 프로젝트 로컬 항목은 `T-027`부터). 함정은 세 층 중 하나에 산다:

| 층 | 위치 | 언제 |
|---|---|---|
| **프로젝트 로컬** | 이 파일 | 이 레포에서 처음 만난 함정 |
| **글로벌 원칙** | `~/.claude/CLAUDE.md` | 어느 레포에서든 재발하는 함정 → 원칙으로 승격 |
| **하드 가드(훅)** | `~/.claude/hooks/*.ps1` | 사람이 규칙을 알아도 실수하는 함정 → 자동 차단 |

**승격 기준**: 같은 함정을 **다른 맥락에서 2회 이상** 만나거나, 실수 비용이 크고 조용히 틀리는(무성 실패) 종류면 → 위층으로 올린다. 승격하면 이 파일엔 "→ 글로벌/훅으로 승격됨" 포인터만 남긴다.

---

## ✅ 이미 상위 층에서 처리된 것들 (여기서 재번호 안 함)

이 머신(Windows 11 + PowerShell 5.1 + Git Bash + 한국어)의 인코딩 함정들은 이미 **글로벌 CLAUDE.md**로 승격돼 있다 — 셋 다 "에러 없이 조용히 틀리는" 무성 실패라 선제 원칙화됨:

- **Git Bash grep/sed 멀티바이트 무성 실패** (C 로케일, 한글 패턴 0건 처리) → 텍스트 검색·치환은 ripgrep / PowerShell 리터럴. *(글로벌 「Windows 셸 한글·인코딩 원칙」 1)*
- **PowerShell 5.1 인라인 인자 한글 CP949 깨짐** (native exe에 넘기는 인라인 경로) → UTF-8 파일 경유(`-F`/stdin), 검증은 바이트(HEX) 대조. *(글로벌 원칙 2 / `T-026` = 한글 커밋 메시지 `.commit-msg-tmp`)*
- **파일 재생성 시 BOM·EOL 소실 → phantom diff** → 원본 BOM·EOL 감지 후 보존, 새 `.ps1`은 UTF-8 BOM 포함. *(글로벌 원칙 3)*

승격된 **하드 가드(훅)** 예:
- `block-main-edit.ps1` (PreToolUse) — main/master 브랜치에서 Write/Edit 차단 → "main 직접 작업 금지" 규칙의 자동화.
- `remind-korean.ps1` (UserPromptSubmit) — 매 턴 "사용자 대면 한국어" 지시 재주입.

---

## 프로젝트 로컬 함정

### T-027 · Windows 콘솔 cp949에서 Python `print`가 em-dash에 `UnicodeEncodeError`

- **증상**: `benchmarks/stt` 리포트 CLI를 Windows 콘솔에서 실행하면, `—`(em-dash)·`…` 같은 비-CP949 문자를 출력하는 순간 `UnicodeEncodeError: 'cp949' codec can't encode character` 로 죽는다. 정작 채점 로직은 멀쩡한데 **출력 단계**에서만 터진다.
- **원인**: Windows 기본 콘솔 코드페이지가 CP949라 Python `sys.stdout` 인코딩이 UTF-8이 아니다. 리포트 텍스트(한글 + 타이포그래피 기호)에 CP949 밖 문자가 섞이면 인코딩 실패.
- **해결**: CLI 진입점에서 stdout을 UTF-8로 재설정.
  ```python
  try:
      sys.stdout.reconfigure(encoding="utf-8")
  except (AttributeError, ValueError):
      pass  # 파이프/리다이렉트 등 reconfigure 불가 환경은 조용히 통과
  ```
- **재발 방지**: 사용자 대면 텍스트를 콘솔로 뿌리는 새 Python 진입점은 **처음부터** 이 재설정을 넣는다. (파일에 쓸 땐 `open(..., encoding="utf-8")`을 명시해 같은 함정 회피.)
- **관련**: 이건 글로벌 원칙 2(PowerShell→native exe 인라인 인자)와 **다른 갈래** — 저건 인자 전달, 이건 Python 프로세스 자신의 stdout. 파이썬 진입점이 늘어나 재발하면 글로벌 원칙으로 승격 고려.

### T-028 · 필드 setter 가드 비대칭 — 새 setter에 (빈값+중복) 가드 누락 시 무성 last-wins/데이터 손실

- **증상**: `synth.py` `_parse_fields`에서 `key=`는 헬퍼 `_set_key`가 (빈 값·중복) 둘 다 막는데, 나중에 추가한 `aliases=`/`canonical=`는 그 가드 없이 `x = val`로 **바로 대입**했다. 그래서 같은 필드를 두 번 주면 앞 값이 **조용히 소실**(last-wins)되고, 빈 값이면 surface로 **무성 fallback**한다. 골든이 에러 없이 틀림 — 예: `aliases=Lumi|aliases=루미에` → 골든에 `루미에`만 남아 채점기가 "Lumi" 전사를 **가짜 CTER 오류**로 집계. 테스트·검증 게이트는 통과하니 무성.
- **원인**: 기존 setter(`_set_key`)의 방어 규약을 **신규 필드 핸들러에 복제하지 않음**. "무성 실패 차단"이 이 빌더의 핵심 계약인데 새 필드만 계약 밖. 곁가지로, 무명 key 하위호환 판정을 `raw enumerate 인덱스 == 0`으로 해 선행 빈 필드가 있으면 유효 key를 'unknown field'로 **오거부**.
- **해결**: 새 명명 필드마다 `_set_key`와 **동일 패턴** — ① 빈 값 → 에러 ② 이미 설정됨(sentinel: `None`/`()`) → 중복 에러. 무명 위치 판정은 raw 인덱스 대신 **'첫 비어있지 않은 필드'**(`first` 플래그, 빈 필드 skip과 정합). *(`/code-review ultra` 2라운드, 6종 확정 — 상세 changeLog 2026-07-12.)*
- **재발 방지**: 파서/빌더에 "필드 → 상태" setter를 **추가**할 땐, 기존 setter의 (빈값+중복+위치) 가드를 그대로 미러링한다. 가드 없는 단순 대입(`x = val`)은 **무성 last-wins의 냄새** — 리뷰에서 이 패턴을 우선 의심.
- **관련**: 이 프로젝트 `stt_bench/synth.py` `_parse_fields`. 다른 파서/빌더에서 2회+ 재발하면 글로벌 원칙("신규 setter는 기존 setter 가드 미러링")으로 승격 검토. (개념 이해가 아니라 해결법이라 여기 Trap; 무성 실패 계열이라 선제 기록.)

---

## 🔄 갱신 정책

- **1분 이상 디버깅**했으면 원인이 잡힌 직후 여기 한 항목(증상/원인/해결/재발방지)을 남긴다.
- 같은 함정을 **다시** 만나면 그 항목에 "재발 N회"를 표시하고, 2회 넘으면 승격 기준에 걸리므로 글로벌/훅 승격을 검토한다.
- 승격 후엔 본문을 지우지 말고 "→ 승격됨" 포인터로 축약해 이력을 보존한다.
