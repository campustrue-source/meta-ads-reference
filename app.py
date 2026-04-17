"""Streamlit UI: Meta ad video URL -> Whisper transcript -> Notion DB."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Force UTF-8 stdio on Windows so Korean filenames/paths don't explode under cp949.
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

STEP_LABELS = [
    ("download", "영상 다운로드"),
    ("audio", "오디오 추출"),
    ("transcribe", "Whisper 스크립트 추출"),
    ("notion", "Notion 적재"),
]
STEP_ICONS = {"pending": "⏳", "done": "✅", "error": "❌"}


def _check_environment() -> list[str]:
    """Return a list of human-readable problems with the local environment."""
    problems: list[str] = []
    for key in ("OPENAI_API_KEY", "NOTION_TOKEN", "NOTION_DATABASE_ID"):
        if not os.getenv(key):
            problems.append(f"환경변수 `{key}` 가 설정되지 않았습니다. `.env` 파일을 확인하세요.")
    if not shutil.which("ffmpeg"):
        problems.append(
            "`ffmpeg` 실행 파일을 찾을 수 없습니다. Windows: `winget install Gyan.FFmpeg` 로 설치한 뒤 터미널을 재시작하세요."
        )
    return problems


def _init_session() -> None:
    st.session_state.setdefault("running", False)
    st.session_state.setdefault("steps", {key: "pending" for key, _ in STEP_LABELS})
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("error", None)
    st.session_state.setdefault("run_counter", 0)


def _reset_run_state() -> None:
    st.session_state.steps = {key: "pending" for key, _ in STEP_LABELS}
    st.session_state.result = None
    st.session_state.error = None


def _render_stepper(placeholder) -> None:
    lines = []
    for key, label in STEP_LABELS:
        status = st.session_state.steps.get(key, "pending")
        lines.append(f"{STEP_ICONS[status]} **{label}**")
    placeholder.markdown("\n\n".join(lines))


def _set_step(key: str, status: str, stepper_placeholder) -> None:
    st.session_state.steps[key] = status
    _render_stepper(stepper_placeholder)


def run_pipeline(name: str, url: str, stepper_placeholder) -> None:
    """Run download -> audio -> transcribe -> notion, updating the UI as we go."""
    video_path: Path | None = None
    audio_path: Path | None = None

    try:
        validate_url(url)

        # 1. Download
        try:
            video_path = download_video(url, DOWNLOAD_DIR)
            _set_step("download", "done", stepper_placeholder)
        except (ValueError, DownloadError) as exc:
            _set_step("download", "error", stepper_placeholder)
            st.session_state.error = ("영상 다운로드", str(exc))
            return

        # 2. Audio extraction
        try:
            audio_path = extract_audio(video_path)
            _set_step("audio", "done", stepper_placeholder)
        except AudioExtractionError as exc:
            _set_step("audio", "error", stepper_placeholder)
            st.session_state.error = ("오디오 추출", str(exc))
            return

        # 3. Whisper transcription
        try:
            transcript = transcribe(audio_path)
            _set_step("transcribe", "done", stepper_placeholder)
        except TranscriptionError as exc:
            _set_step("transcribe", "error", stepper_placeholder)
            st.session_state.error = ("Whisper 스크립트 추출", str(exc))
            return

        # 4. Notion append
        try:
            db_id = os.getenv("NOTION_DATABASE_ID", "")
            page_url = append_to_database(db_id, name, transcript, url)
            _set_step("notion", "done", stepper_placeholder)
        except NotionError as exc:
            _set_step("notion", "error", stepper_placeholder)
            st.session_state.error = ("Notion 적재", str(exc))
            return

        st.session_state.result = {"transcript": transcript, "page_url": page_url}
    finally:
        cleanup(video_path, audio_path)


def main() -> None:
    st.set_page_config(page_title="Meta 광고 트랜스크립터", page_icon="🎬", layout="centered")
    _init_session()

    st.title("🎬 Meta 광고 → Notion 트랜스크립터")
    st.caption("Meta CDN 또는 Ad Library URL을 붙여넣으면 Whisper로 스크립트를 뽑아 Notion DB에 저장합니다.")

    problems = _check_environment()
    if problems:
        st.error("실행 전에 다음을 해결해주세요:")
        for p in problems:
            st.markdown(f"- {p}")
        st.stop()

    # The run_counter in the widget key lets us reset the form cleanly after each run.
    run_id = st.session_state.run_counter
    with st.form(key=f"run_form_{run_id}"):
        name = st.text_input(
            "릴스 이름",
            placeholder="예: 포커스인_20250101_A안",
            disabled=st.session_state.running,
        )
        url = st.text_area(
            "영상 URL",
            placeholder="https://video-*.xx.fbcdn.net/... 또는 https://www.facebook.com/ads/library/?id=...",
            height=100,
            disabled=st.session_state.running,
        )
        submit = st.form_submit_button(
            "실행",
            disabled=st.session_state.running,
            use_container_width=True,
        )

    stepper_placeholder = st.empty()
    _render_stepper(stepper_placeholder)

    if submit and not st.session_state.running:
        if not name.strip():
            st.warning("릴스 이름을 입력해주세요.")
        elif not url.strip():
            st.warning("영상 URL을 입력해주세요.")
        else:
            st.session_state.running = True
            _reset_run_state()
            _render_stepper(stepper_placeholder)
            try:
                run_pipeline(name.strip(), url.strip(), stepper_placeholder)
            finally:
                st.session_state.running = False
                st.session_state.run_counter += 1
            st.rerun()

    if st.session_state.error:
        stage, message = st.session_state.error
        st.error(f"❌ **{stage}** 단계에서 실패했습니다.\n\n{message}")

    if st.session_state.result:
        page_url = st.session_state.result["page_url"]
        transcript = st.session_state.result["transcript"]
        st.success("완료! Notion에 저장됐습니다.")
        st.markdown(f"🔗 [Notion 페이지 열기]({page_url})")
        with st.expander("추출된 스크립트 미리보기", expanded=True):
            st.text_area(
                label="스크립트",
                value=transcript,
                height=300,
                label_visibility="collapsed",
            )


if __name__ == "__main__":
    main()
