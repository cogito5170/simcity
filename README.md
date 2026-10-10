# SimCity 3000 — AI Mayor

AI가 도시를 **관찰 → 판단 → 행동 → 평가**하며 스스로 운영하는 실험 프로젝트입니다.
지금은 **1단계: 관찰과 기록**만 구현되어 있습니다. 게임에 입력을 보내는 코드는 아직 없습니다.

> 실제로 확인한 것과 아직 확인하지 못한 것의 구분은 [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md)에 있습니다.
> 단계별 설계는 [`docs/ROADMAP.md`](docs/ROADMAP.md)에 있습니다.

## 지금 할 수 있는 일

| 명령 | 하는 일 |
|---|---|
| `python -m mayor doctor` | 환경 점검: 게임 창 찾기, 캡처 1장 시험, 검은 화면 여부, OCR/API 키 확인 |
| `python -m mayor install "<게임 폴더>"` | 설치 폴더 점검(읽기 전용): 판본(원판/Unlimited), GZCOM 플러그인 지원 흔적, 파일 빌드 정보 |
| `python -m mayor windows` | 열려 있는 모든 창(제목·exe·크기) 나열. 게임 창 이름이 예상과 다를 때 사용 |
| `python -m mayor calibrate` | 현재 화면 + 좌표 격자 이미지 저장. OCR 영역(ROI)을 잡는 데 사용 |
| `python -m mayor observe` | 주기적으로 관찰하고 `runs/run-*/log.jsonl`에 도시 상태 기록 |
| `python -m mayor report runs/run-…` | 기록된 상태를 표로 출력 |

### 도시 상태는 "본 것"과 "모르는 것"을 구분해 기록합니다

모든 값에는 상태가 붙습니다.

- `observed`: 이번 화면에서 읽음. 출처(`ocr:funds`, `llm:gemini/...`)와 근거 포함
- `stale`: 이전에 읽은 값을 이어 씀. `observed_at`에 읽은 시각이 남음
- `conflict`: OCR과 비전 모델의 판독이 서로 다름. 값은 비우고 두 판독을 모두 기록
- `rejected`: 판독은 됐지만 검증에 실패함 (예: 음수 인구)
- `unknown`: 신뢰할 만한 판독이 없음

`report` 출력에서는 `20000` = 방금 봄, `20000*` = 예전 값, `?` = 모름으로 표시됩니다.

## Windows에서 시작하기

1. **Python 3.9 이상**이 필요합니다. 이미 설치되어 있으면 다시 설치하지 않아도 됩니다.
   설치 여부는 명령 프롬프트에서 `python --version`으로 확인합니다.
2. **코드를 내려받습니다.** 둘 중 하나를 쓰면 됩니다.
   - ZIP: GitHub에 로그인한 상태에서
     https://github.com/cogito5170/simcity/archive/refs/heads/claude/epic-bardeen-gv5bls.zip
     을 내려받아 압축을 풉니다. 폴더 이름은 `simcity-claude-epic-bardeen-gv5bls`입니다.
   - git: `git clone -b claude/epic-bardeen-gv5bls https://github.com/cogito5170/simcity.git`
3. 명령 프롬프트에서 **그 폴더로 이동**합니다. `mayor` 폴더와 `README.md`가 보이는 위치여야 합니다.
   ```
   cd C:\Users\<이름>\Downloads\simcity-claude-epic-bardeen-gv5bls
   dir
   ```
   다른 폴더에서 실행하면 `No module named mayor` 오류가 납니다.
4. 필요한 라이브러리를 설치합니다.
   ```
   python -m pip install -r requirements.txt
   ```
5. 게임 설치 폴더를 점검합니다. 이 명령은 Pillow 없이도 동작합니다.
   ```
   python -m mayor install "C:\게임\설치\폴더"
   ```
6. SimCity 3000을 실행하고 도시를 연 상태에서 다음을 실행합니다.
   ```
   python -m mayor doctor
   ```
   - `game window: NOT FOUND`가 나오면 `python -m mayor windows`로 실제 창 제목과 exe 이름을 확인합니다.
     그다음 `config.example.json`을 `config.json`으로 복사하고 `window_title_pattern`이나 `window_exe`를 고칩니다.
   - `doctor`는 두 가지 캡처 방식(`rect`: 화면 영역, `hwnd`: 창 자체 이미지)을 모두 시험하고, 되는 쪽을 `capture_method`로 추천합니다.
   - 두 방식 모두 `BLANK`가 나오면 전체화면(DirectDraw) 출력이 캡처되지 않는 경우일 수 있습니다.
     창 모드를 시도해 보세요 (`docs/ENVIRONMENT.md` 참고).
   - `runs/doctor-*/capture_*.png`를 열어 **게임 화면이 실제로 찍혔는지** 눈으로 확인합니다.
7. 상태 읽기 방법을 고릅니다. 둘 다 써도 됩니다.
   - **무료: 로컬 OCR.** [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)를 설치하고 `pip install pytesseract`를 실행합니다.
     그다음 `python -m mayor calibrate`가 만든 `grid.png`를 보고 자금·인구·날짜가 표시되는 영역을
     `config.json`의 `"rois"`에 0~1 비율 좌표로 적습니다. 다시 `calibrate`를 실행해 `roi_*.png` 조각을 확인하세요.
   - **저비용: 비전 모델.** API 키를 하나만 설정합니다 (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` 중 하나).
     ```
     set GEMINI_API_KEY=...
     python -m mayor observe --extract llm --provider gemini --once
     ```
8. 계속 기록하려면 다음을 실행합니다.
   ```
   python -m mayor observe --extract ocr,llm
   ```
   Ctrl+C로 멈춥니다.

### 비용을 아끼는 장치 (모두 `config.json`의 `llm` 항목)

- 비전 모델 호출은 **기본 5분에 최대 1회**이고, 그마저도 **직전 호출 이후 화면이 바뀌었을 때만** 합니다 (`min_interval_s`, `min_change`).
- 실행 1회당 **호출 수 상한**은 `max_calls_per_run`(기본 50)입니다.
- 이미지는 가로 1024px 이하로 줄여서 보냅니다 (`max_width`).
- 검은 화면이나 게임 창이 없는 상태에서는 호출하지 않습니다.
- 각 호출의 토큰 사용량은 `log.jsonl`에, 합계는 `summary.json`에 남습니다.
- 기본 모델은 각 회사의 저가형 모델입니다 (`mayor/llm/__init__.py`의 `DEFAULT_MODELS`).
  모델 이름은 자주 바뀌므로 `--model` 옵션으로 바꿀 수 있습니다.

## 구조

```
mayor/
  winenv.py        Windows 창 탐색 (ctypes, 읽기 전용)
  sources.py       프레임 소스: 게임 창 / 전체 화면 / 이미지 파일(재생·테스트용)
  frame_check.py   코드로 하는 점검: 검은 화면, 변화율 (AI 불필요)
  state.py         도시 상태 모델과 숫자·수준 파싱
  extractors/
    roi_ocr.py     무료 로컬 OCR (Tesseract, 사용자가 보정한 영역)
    vision.py      비전 LLM 판독: "보이는 것만, 근거와 함께, 안 보이면 null"
    merge.py       판독 병합(일치하면 신뢰도↑, 다르면 conflict), 검증, stale 이월
  llm/             공급자 교체 가능: anthropic / gemini / openai / mock (urllib만 사용, SDK 불필요)
  observer.py      관찰 루프: 캡처 → 점검 → 판독 → 병합 → log.jsonl
  cli.py           명령행
tools/make_mock_frames.py   테스트용 "합성" 화면 생성기 (실제 게임 화면 아님)
tests/                       python -m unittest discover -s tests
```

AI 모델을 바꾸려면 `--provider`/`--model` 옵션이나 설정만 고치면 됩니다. 나머지 코드는 `VisionLLM.ask(prompt, png)`라는 인터페이스 하나에만 의존합니다.

## 테스트

```
python -m unittest discover -s tests
```

합성 프레임과 mock LLM으로 다음을 검증합니다: 파이프라인 전체, 변화 감지, 검은 화면 처리, 호출 간격 및 상한, 판독 충돌 처리, 공급자별 요청 형식.
