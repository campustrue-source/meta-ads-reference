# Meta 광고 → Notion 트랜스크립터

Meta(Facebook/Instagram) 광고 영상의 CDN URL 또는 Ad Library URL을 붙여넣으면
- 영상을 다운로드하고
- OpenAI Whisper로 스크립트를 추출한 뒤
- Notion DB에 새 row로 저장하는

로컬 Streamlit 웹앱입니다.

---

## 사전 준비

### 1. ffmpeg 설치 (Windows 기준)

```powershell
winget install Gyan.FFmpeg
```

설치 후 **터미널을 반드시 재시작**하고 `ffmpeg -version` 이 동작하는지 확인하세요.

### 2. Python 3.13 + 의존성

```bash
py -m pip install -r requirements.txt
```

### 3. `.env` 파일 생성

프로젝트 루트에서:

```bash
copy .env.example .env
```

그 뒤 `.env` 의 세 값을 채웁니다.

```
OPENAI_API_KEY=sk-...
NOTION_TOKEN=secret_...
NOTION_DATABASE_ID=...
```

---

## Notion 세팅

### Step 1 — DB 만들기

Notion 에서 새 페이지를 만든 뒤 `/database` 로 **Full page database** 를 하나 만드세요. 다음 속성을 갖추면 됩니다.

| 속성 이름       | 속성 타입   | 필수 여부 |
|-----------------|-------------|-----------|
| `릴스 이름`     | Title       | 필수      |
| `본문 스크립트` | Rich text   | 권장      |
| `원본 URL`      | URL         | 선택      |
| `수집일`        | Date        | 선택      |

속성 이름은 **정확히 한글로** 작성해야 합니다(앱이 그 이름을 찾습니다). `본문 스크립트` 속성이 없으면 스크립트는 대신 페이지 본문에 저장됩니다.

### Step 2 — Integration 만들기

1. <https://www.notion.so/profile/integrations> 접속
2. **New integration** 클릭
3. 이름 입력 → **Internal** 선택 → Associated workspace 선택 → **Save**
4. `Internal Integration Secret` 값을 복사해 `.env` 의 `NOTION_TOKEN` 에 붙여넣기

### Step 3 — DB 에 Integration 연결

1. Step 1 에서 만든 DB 페이지 열기
2. 오른쪽 위 `···` 메뉴 → **Connections** → Step 2 에서 만든 Integration 선택

### Step 4 — Database ID 추출

DB 를 브라우저에서 열면 URL이 이렇게 생겼습니다:

```
https://www.notion.so/<workspace>/<DATABASE_ID>?v=<VIEW_ID>
```

`<DATABASE_ID>` 부분(32자리 hex, 하이픈 유무 무관)을 `.env` 의 `NOTION_DATABASE_ID` 에 붙여넣습니다.

---

## 실행

```bash
py -m streamlit run app.py
```

브라우저에서 <http://localhost:8501> 이 자동으로 열립니다.

### 사용법

1. **릴스 이름** 에 광고를 식별할 이름을 입력 (예: `포커스인_20250101_A안`)
2. **영상 URL** 에 다음 중 하나를 붙여넣기
   - Meta CDN URL: `https://video-*.xx.fbcdn.net/...`
   - Ad Library URL: `https://www.facebook.com/ads/library/?id=...`
3. **실행** 버튼 클릭
4. 진행 상황 스테퍼가 단계별로 업데이트됩니다
   - ⏳/✅/❌ 영상 다운로드 / 오디오 추출 / Whisper / Notion
5. 성공 시 스크립트 미리보기와 Notion 페이지 링크가 표시됩니다

### 트러블슈팅

| 증상 | 원인 / 대응 |
|------|-------------|
| "URL이 만료됐을 수 있습니다" | Meta CDN URL은 수 시간 내 만료됩니다. Ad Library 에서 새로 복사하세요. |
| "ffmpeg 를 찾을 수 없습니다" | `winget install Gyan.FFmpeg` 후 터미널 재시작 |
| "OPENAI_API_KEY가 유효하지 않습니다" | `.env` 의 키 확인, 또는 OpenAI 콘솔에서 재발급 |
| "Notion DB 조회 실패" | Integration 이 해당 DB 에 Connect 되어 있는지, DB ID 가 정확한지 확인 |
| "Whisper 파일 크기 제한(25MB) 초과" | 보통 앱이 자동 재압축하지만, 영상이 매우 길면 실패할 수 있습니다. 영상을 자른 뒤 재시도 |

---

## 디렉토리 구조

```
meta-ad-transcriber/
├── .env.example
├── .gitignore
├── requirements.txt
├── app.py                  # Streamlit UI
├── src/
│   ├── __init__.py
│   ├── downloader.py       # 다운로드 + ffmpeg 오디오 추출
│   ├── transcriber.py      # OpenAI Whisper 호출
│   └── notion_client.py    # Notion DB 적재
├── downloads/              # 임시 파일 (자동 정리)
└── README.md
```

## 각 모듈 단독 실행

개발 중 단계별로 테스트하고 싶을 때:

```bash
# 다운로드 + 오디오 추출
py -m src.downloader "https://video-xxx.fbcdn.net/..."

# Whisper 만 테스트 (기존 mp3 파일 필요)
py -m src.transcriber downloads/test.mp3

# Notion 저장 테스트
py -m src.notion_client "테스트 타이틀" "테스트 스크립트 내용"
```

## 제약 사항

- 로컬 실행 전용. 외부 호스팅 금지.
- 인증/로그인 없음. 본인 PC 에서만 사용.
- DB 나 Redis 같은 추가 인프라 없이 파일시스템만 사용.
