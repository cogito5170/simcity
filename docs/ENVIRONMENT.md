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
| 창 모드 전환 방법 | 근거 있음, 사용자 PC에서는 미확인. sc3k-extra-cheats 작성자가 Windows판을 `-w -r1280x1024 -intro:off` 인자로 창 모드 실행했다고 적어 둠. Loki readme에는 Alt+Enter도 있음. 그 밖에 DxWnd, cnc-ddraw 사용 사례가 있음 | 바로가기에 인자를 넣어 실행 |
| HUD에서 자금·인구·날짜가 표시되는 위치 | **가정하지 않음.** 코드에 좌표가 들어 있지 않음 | `calibrate`로 측정 |
| 비전 모델이 SC3 HUD 글자를 정확히 읽는지 | 미확인 | `observe --extract llm --once` 결과를 `capture.png`와 대조 |
| 기본 모델 이름 (`claude-haiku-5-5`, `gemini-2.5-flash-lite`, `gpt-4.1-mini`) | 앞의 하나는 확인함. Gemini·OpenAI 이름은 웹 검색 결과 기준이라 은퇴했을 수 있음 | 오류가 나면 `--model`로 교체 |
| 고DPI 화면에서 창 좌표와 캡처 좌표가 일치하는지 | `SetProcessDpiAwareness(2)`로 처리했으나 미확인 | `capture.png`가 정확히 게임 영역만 담는지 확인 |

## 4. 플러그인(DLL) 방식 조사 (2026-10-10 추가)

화면을 읽는 대신 게임 안에서 값을 직접 읽는 방법이 있는지 조사했습니다.
아래는 **공개된 커뮤니티 소스 코드를 직접 읽고 확인한 내용**입니다. 사용자 PC에서는 아직 확인하지 않았습니다.

### 확인한 것 (소스 코드 기준)
- **SC3U.exe는 `Apps` 폴더의 DLL을 스스로 불러옵니다.** Maxis의 GZCOM 프레임워크(SimCity 4 플러그인 구조의 전신)를 통해서입니다.
  실행 파일을 고칠 필요가 없고, 지우면 원상태로 돌아갑니다.
  - [0xC0000054/sc3k-gzcom-dll](https://github.com/0xC0000054/sc3k-gzcom-dll): 플러그인 개발용 헤더와 프레임워크입니다 (LGPL-2.1).
    Linux판 SC3U에 남아 있던 디버그 심볼로 인터페이스를 복원했습니다.
  - [0xC0000054/sc3k-extra-cheats](https://github.com/0xC0000054/sc3k-extra-cheats): 위 프레임워크로 만든 Windows용 플러그인입니다 (MIT, Simtropolis에서 배포 중).
    `GetCity()` → `BudgetLayer()` → `SetTotalFunds()`와 `GetDate()`/`SetDate()`를 실제로 호출합니다.
  - [DenisSergeevitch/simcity3000-gta-mode](https://github.com/DenisSergeevitch/simcity3000-gta-mode): 같은 로딩 방식의 대형 플러그인입니다.
    매 프레임 게임 스레드에서 실행되며, 파일 기반 명령 채널(`gta_cmd.txt` → `gta_out.txt`)을 씁니다.
- 헤더에 선언된 읽기 함수 중 이 프로젝트에 쓸모 있는 것:
  - `cISC3City`: `GetDate`, `GetGameSpeed`, `IsPaused`, `IsDisasterActive`, `GetCityName`, 각 레이어와 조언자 접근자
  - `cISC3BudgetLayer`: `GetTotalFunds`, `GetYTDIncome`, `GetEstIncome`, `GetYTDExpenses`, `GetEstExpenses`, `GetTaxRate`, `GetTotalBorrowed`, `GetDepartmentBudget`
  - `cISC3DepartmentBudget`: `GetFundingPercentage`, `SetFundingPercentage`, `GetTotalFundingNeeded`
  - `cISC3ResidentialLayer`: `GetPopulation`, 교육·보건 등급과 수용량, 파업 여부
  - `cISC3PollutionLayer`: 평균 대기·수질 오염, 미수거 쓰레기, 매립지 잔여 용량
  - `cISC3Valve`: 수요(valve) 값. 다만 R/C/I 각각의 valve ID는 **알려져 있지 않습니다.**
- 쓰기(행동)에 쓸 수 있는 함수: `SetFundingPercentage`(부서별 예산), `SetGameSpeed`, `Pause`/`Unpause`.
  **세율을 바꾸는 함수는 헤더에 없습니다** (`GetTaxRate`만 있음).
  건물·구역 배치 API도 확인되지 않았습니다.

### 아직 모르는 것과 위험
- 대상은 **SimCity 3000 Unlimited(SC3U.exe)** 입니다. 1999년 원판 SC3에서도 같은 방식으로 플러그인을 불러오는지는 확인하지 못했습니다.
- 헤더는 Linux판 심볼에서 만든 것입니다. Windows판에서 실제로 쓰임이 확인된 것은 extra-cheats가 호출하는 함수들뿐입니다.
  나머지 함수(인구, 오염 등)는 **같은 배치일 것이라는 추정**입니다. 헤더 일부에는 "반환형 확인 필요" 주석도 붙어 있습니다.
- GTA 모드의 메모리 주소 기반 기능은 Steam판 특정 빌드에만 맞습니다. GZCOM 인터페이스 호출은 그보다 버전 영향이 적을 것으로 보이지만, 이것도 확인이 필요합니다.
- 잘못된 호출은 게임을 강제 종료시킬 수 있습니다. 그래서 **읽기 전용부터 시작**하고, 세이브 파일은 백업한 뒤 실험해야 합니다.
- 빌드에는 32비트 C++ 컴파일러가 필요합니다. Visual Studio 2022 Community(무료) 또는 mingw-w64 i686을 쓸 수 있습니다.

## 5. 조사 근거와 가정하지 않은 것

- 게임 내부 함수는 **4절에서 소스 코드로 확인한 GZCOM 인터페이스만** 고려합니다. 메모리 주소를 직접 읽는 방식은 버전 의존성이 커서 쓰지 않습니다.
- 게임 단축키도 아직 사용하지 않습니다. 2단계(조작)에 들어가기 전에 실제 게임에서 하나씩 확인한 뒤 목록화할 예정입니다.
- 화면 판독은 "사람이 보는 화면"만을 근거로 합니다. 이것이 가장 확실하게 가능한 경로이기 때문입니다.

참고 자료 (웹 검색 결과):
- Loki SC3U FAQ/readme (실행 인자 `-w`, Alt+Enter 언급): https://faqs.lokigames.twolife.be/sc3ufaq_print.html
- GOG 포럼, DxWnd로 SC3 창 모드 실행 사례: https://www.gog.com/forum/simcity_series/fixed_these_problems_using_dxwnd_map_scrolling_speed_high_cpu_usage_small_textui/post3
