"""FastAPI UI: same pipeline as app.py, but served as static HTML + SSE."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from src.downloader import (
    AudioExtractionError,
    DownloadError,
    cleanup,
    download_video,
    extract_audio,
    validate_url,
)
from src.notion_client import NotionError, append_to_database
from src.transcriber import TranscriptionError, transcribe

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = APP_DIR / "downloads"
STATIC_DIR = APP_DIR / "static"

app = FastAPI(title="Meta Ad Transcriber")

_basic = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    password = os.getenv("APP_PASSWORD", "")
    if not password:
        return
    username = os.getenv("APP_USERNAME", "admin")
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="meta-ad-transcriber"'},
        )
    ok_user = secrets.compare_digest(credentials.username, username)
    ok_pass = secrets.compare_digest(credentials.password, password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="meta-ad-transcriber"'},
        )


@app.get("/api/health")
def health() -> dict[str, Any]:
    problems: list[str] = []
    for key in ("OPENAI_API_KEY", "NOTION_TOKEN", "NOTION_DATABASE_ID"):
        if not os.getenv(key):
            problems.append(f"환경변수 `{key}` 가 설정되지 않았습니다. `.env` 파일을 확인하세요.")
    if not shutil.which("ffmpeg"):
        problems.append(
            "`ffmpeg` 실행 파일을 찾을 수 없습니다. Windows: `winget install Gyan.FFmpeg` 로 설치한 뒤 터미널을 재시작하세요."
        )
    return {"ok": not problems, "problems": problems}


class RunRequest(BaseModel):
    name: str
    url: str


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


_END = "__end__"


@app.post("/api/run", dependencies=[Depends(require_auth)])
async def run(req: RunRequest):
    name = (req.name or "").strip()
    url = (req.url or "").strip()
    if not name:
        raise HTTPException(400, "릴스 이름을 입력해주세요.")
    if not url:
        raise HTTPException(400, "영상 URL을 입력해주세요.")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    def emit(event: str, **data: Any) -> None:
        asyncio.run_coroutine_threadsafe(queue.put((event, data)), loop)

    def worker() -> None:
        video_path: Path | None = None
        audio_path: Path | None = None
        try:
            try:
                validate_url(url)
            except ValueError as exc:
                emit("error", stage="입력 검증", message=str(exc))
                return

            emit("step", key="download", status="running")
            try:
                video_path = download_video(url, DOWNLOAD_DIR)
                emit("step", key="download", status="done")
            except (ValueError, DownloadError) as exc:
                emit("step", key="download", status="error")
                emit("error", stage="영상 다운로드", message=str(exc))
                return

            emit("step", key="audio", status="running")
            try:
                audio_path = extract_audio(video_path)
                emit("step", key="audio", status="done")
            except AudioExtractionError as exc:
                emit("step", key="audio", status="error")
                emit("error", stage="오디오 추출", message=str(exc))
                return

            emit("step", key="transcribe", status="running")
            try:
                transcript = transcribe(audio_path)
                emit("step", key="transcribe", status="done")
            except TranscriptionError as exc:
                emit("step", key="transcribe", status="error")
                emit("error", stage="Whisper 스크립트 추출", message=str(exc))
                return

            emit("step", key="notion", status="running")
            try:
                db_id = os.getenv("NOTION_DATABASE_ID", "")
                page_url = append_to_database(db_id, name, transcript, url)
                emit("step", key="notion", status="done")
            except NotionError as exc:
                emit("step", key="notion", status="error")
                emit("error", stage="Notion 적재", message=str(exc))
                return

            emit("done", transcript=transcript, page_url=page_url)
        finally:
            cleanup(video_path, audio_path)
            emit(_END)

    async def stream() -> AsyncGenerator[bytes, None]:
        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                event, data = await queue.get()
                if event == _END:
                    break
                yield _sse(event, data)
        finally:
            await task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/", dependencies=[Depends(require_auth)])
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
