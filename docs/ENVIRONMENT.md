# 환경 조사 결과: 확인한 것과 확인하지 못한 것

작성일: 2026-10-10

## 1. 이번 작업의 실행 환경 (확인함)

- 이 코드는 **클라우드의 Linux 컨테이너**(Ubuntu 24.04, Python 3.13, Pillow 12)에서 작성하고 테스트했습니다.
- 이 세션에서는 **사용자의 Windows PC에 접근할 수 없었습니다.** 원격 데스크톱이나 화면 제어 도구가 연결되어 있지 않았습니다.
  그래서 다음은 **확인하지 못했습니다.**
  - SimCity 3000이 설치되어 있는지, 실행 중인지
  - 게임 버전 (원판 / Unlimited / GOG·Steam·EA판), 해상도, 창 모드 여부
  - 실제 창 제목과 실행 파일 이름
  - 실제 화면에서 캡처가 되는지 (검은 화면 여부)
- 그래서 첫 프로토타입에 **`doctor` 명령**을 넣었습니다. Windows PC에서 실행하면 위 항목을 직접 점검하고
  `runs/doctor-*/doctor.json`과 `capture.png`로 결과를 남깁니다.
  **다음 단계는 이 결과를 보고 결정해야 합니다.**
- 저장소는 비어 있었습니다 (커밋 없음).
- 이 컨테이너에서는 Tesseract와 pytest를 설치할 수 없었습니다 (패키지 저장소 접근 불가).
  그래서 테스트는 표준 라이브러리 `unittest`로 작성했고, OCR 실제 판독 테스트는 Tesseract가 있을 때만 돌아갑니다.

## 2. 코드에서 검증한 것 (합성 화면 + mock 기준)

- 이미지 파일 소스 → 프레임 점검 → mock 비전 판독 → 병합·검증 → `log.jsonl`/`latest_state.json`/`summary.json` 기록 (전체 경로)
- 같은 화면은 PNG를 다시 저장하지 않고, 비전 모델도 다시 호출하지 않음
- 검은 화면을 감지하면 판독을 생략함. 이때 이전 값은 `stale`로만 남김
- 비전 호출 최소 간격과 호출 수 상한
- OCR과 비전 판독이 다르면 `conflict`로 기록하고 어느 쪽도 고르지 않음
- 잘못된 모델 응답(JSON이 아님, 없는 필드, 잘못된 값)이 와도 예외 없이 `unknown`/`rejected`로 처리함
- Anthropic / Gemini / OpenAI 요청 형식과 응답 파싱. 네트워크 없이 모의 응답으로만 확인했고, **실제 API 호출은 하지 않았습니다** (키 없음)
- `calibrate`의 격자·ROI 이미지 생성

## 3. Windows에서 아직 검증되지 않은 것

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| `winenv.py`의 ctypes 창 탐색 (EnumWindows 등) | 작성만 함. Windows에서 실행해 보지 않음 | `python -m mayor windows` |
| 게임 창 이름. 기본값은 정규식 `sim\s*city\s*3000`, exe 후보는 `SC3U.exe`, `SC3.exe` | **추정값** | `windows` 출력에서 확인한 뒤 `config.json`에 기입 |
| `ImageGrab`(GDI)로 게임 화면을 캡처할 수 있는지 | 미확인. 전체화면 DirectDraw 게임은 검은 화면이 나올 수 있음 | `doctor`의 BLANK 판정과 `capture_*.png` |
| `hwnd` 캡처 방식 (Pillow `ImageGrab.grab(window=...)`) | 미확인. 게임이 다른 창에 가려져도 찍힐 가능성이 있으나, DirectDraw 출력은 검게 나올 수도 있음 | `doctor`가 `rect`·`hwnd` 둘 다 시험 |
| 창 모드 전환 방법 | 미확인. Loki(Linux 이식판) readme에는 `-w` 실행 인자와 Alt+Enter가 적혀 있으나 Windows판에서도 되는지는 모름. 커뮤니티에서는 DxWnd로 창 모드 실행을 했다는 사례가 있음 | 직접 시도 |
| HUD에서 자금·인구·날짜가 표시되는 위치 | **가정하지 않음.** 코드에 좌표가 들어 있지 않음 | `calibrate`로 측정 |
| 비전 모델이 SC3 HUD 글자를 정확히 읽는지 | 미확인 | `observe --extract llm --once` 결과를 `capture.png`와 대조 |
| 기본 모델 이름 (`claude-haiku-5-5`, `gemini-2.5-flash-lite`, `gpt-4.1-mini`) | 앞의 하나는 확인함. Gemini·OpenAI 이름은 웹 검색 결과 기준이라 은퇴했을 수 있음 | 오류가 나면 `--model`로 교체 |
| 고DPI 화면에서 창 좌표와 캡처 좌표가 일치하는지 | `SetProcessDpiAwareness(2)`로 처리했으나 미확인 | `capture.png`가 정확히 게임 영역만 담는지 확인 |

## 4. 조사 근거와 가정하지 않은 것

- 게임 내부 API, 메모리 구조, 스크립트 인터페이스는 **사용하지 않습니다.** 존재하는지 확인된 바가 없습니다.
- 게임 단축키도 아직 사용하지 않습니다. 2단계(조작)에 들어가기 전에 실제 게임에서 하나씩 확인한 뒤 목록화할 예정입니다.
- 화면 판독은 "사람이 보는 화면"만을 근거로 합니다. 이것이 가장 확실하게 가능한 경로이기 때문입니다.

참고 자료 (웹 검색 결과):
- Loki SC3U FAQ/readme (실행 인자 `-w`, Alt+Enter 언급): https://faqs.lokigames.twolife.be/sc3ufaq_print.html
- GOG 포럼, DxWnd로 SC3 창 모드 실행 사례: https://www.gog.com/forum/simcity_series/fixed_these_problems_using_dxwnd_map_scrolling_speed_high_cpu_usage_small_textui/post3
