"""Video download + audio extraction for Meta ad videos."""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

WHISPER_SIZE_LIMIT = 25 * 1024 * 1024  # 25 MiB
DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class DownloadError(Exception):
    """Raised when a video cannot be downloaded."""


class AudioExtractionError(Exception):
    """Raised when ffmpeg fails to produce a usable audio file."""


def is_meta_cdn_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("fbcdn.net") or host.endswith("cdninstagram.com")


def is_ad_library_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path or ""
    return host.endswith("facebook.com") and "/ads/library" in path


def validate_url(url: str) -> None:
    """Raise ValueError if URL is not a supported Meta CDN / Ad Library URL."""
    if not url:
        raise ValueError("URL이 비어 있습니다.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("http 또는 https URL만 지원합니다.")
    if not (is_meta_cdn_url(url) or is_ad_library_url(url)):
        raise ValueError(
            "Meta CDN URL(*.fbcdn.net) 또는 Ad Library URL(facebook.com/ads/library)만 허용됩니다."
        )


def download_video(url: str, dest_dir: Path) -> Path:
    """Download a Meta CDN or Ad Library video to ``dest_dir``.

    Returns the path to the saved mp4 file.
    """
    validate_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{uuid.uuid4().hex}.mp4"

    if is_meta_cdn_url(url):
        _download_direct(url, target)
    else:
        _download_via_ytdlp(url, target)

    if not target.exists() or target.stat().st_size == 0:
        raise DownloadError("다운로드한 파일이 비어 있습니다. URL이 만료됐을 수 있습니다.")
    return target


def _download_direct(url: str, target: Path) -> None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "ko,en-US;q=0.9,en;q=0.8",
        "Referer": "https://www.facebook.com/",
    }
    try:
        with requests.get(url, headers=headers, stream=True, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status_code in (403, 404, 410):
                raise DownloadError(
                    "URL이 만료됐을 수 있습니다. Ad Library에서 새로 복사해주세요."
                )
            resp.raise_for_status()
            with open(target, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        fh.write(chunk)
    except requests.Timeout as exc:
        raise DownloadError("다운로드 타임아웃(30초)이 초과됐습니다.") from exc
    except requests.RequestException as exc:
        raise DownloadError(f"다운로드 실패: {exc}") from exc


def _download_via_ytdlp(url: str, target: Path) -> None:
    try:
        import yt_dlp  # local import so the module stays importable without yt-dlp
    except ImportError as exc:
        raise DownloadError("yt-dlp가 설치되어 있지 않습니다. requirements.txt를 설치하세요.") from exc

    ydl_opts = {
        "outtmpl": str(target),
        "format": "mp4/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": DEFAULT_TIMEOUT,
        "http_headers": {"User-Agent": USER_AGENT},
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:  # yt-dlp raises many subclasses
        msg = str(exc).lower()
        if "expired" in msg or "403" in msg or "404" in msg:
            raise DownloadError(
                "URL이 만료됐을 수 있습니다. Ad Library에서 새로 복사해주세요."
            ) from exc
        raise DownloadError(f"Ad Library 다운로드 실패: {exc}") from exc


def extract_audio(video_path: Path) -> Path:
    """Extract mono 16 kHz mp3 audio, recompressing if it exceeds Whisper's 25 MB cap."""
    if not shutil.which("ffmpeg"):
        raise AudioExtractionError(
            "ffmpeg를 찾을 수 없습니다. 'winget install Gyan.FFmpeg' 로 설치하세요."
        )

    audio_path = video_path.with_suffix(".mp3")
    # Initial pass: 64 kbps is plenty for speech and keeps most files well under 25 MB.
    _run_ffmpeg(video_path, audio_path, bitrate_kbps=64)

    # Fallback: progressively lower bitrate until under the cap.
    for bitrate in (48, 32, 24, 16):
        if audio_path.stat().st_size <= WHISPER_SIZE_LIMIT:
            break
        _run_ffmpeg(video_path, audio_path, bitrate_kbps=bitrate)

    if audio_path.stat().st_size > WHISPER_SIZE_LIMIT:
        raise AudioExtractionError(
            "영상 길이가 너무 깁니다. 최저 비트레이트로도 25MB를 초과했습니다."
        )
    return audio_path


def _run_ffmpeg(src: Path, dst: Path, *, bitrate_kbps: int) -> None:
    if dst.exists():
        dst.unlink()
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", f"{bitrate_kbps}k",
        "-f", "mp3",
        str(dst),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            # Force UTF-8 so Windows cp949 doesn't crash on Korean-filename stderr.
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise AudioExtractionError("ffmpeg 실행 파일을 찾을 수 없습니다.") from exc
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-5:]
        raise AudioExtractionError("ffmpeg 변환 실패: " + " | ".join(tail))


def cleanup(*paths: Path) -> None:
    """Best-effort removal of temporary files."""
    for p in paths:
        try:
            if p and p.exists():
                p.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    # CLI smoke test: python -m src.downloader <url>
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m src.downloader <meta-url>")
        sys.exit(1)
    out_dir = Path(__file__).resolve().parent.parent / "downloads"
    video = download_video(sys.argv[1], out_dir)
    print(f"video: {video} ({video.stat().st_size:,} bytes)")
    audio = extract_audio(video)
    print(f"audio: {audio} ({audio.stat().st_size:,} bytes)")
