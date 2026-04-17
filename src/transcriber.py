"""OpenAI Whisper transcription wrapper."""
from __future__ import annotations

import os
from pathlib import Path

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

WHISPER_MODEL = "whisper-1"
WHISPER_SIZE_LIMIT = 25 * 1024 * 1024


class TranscriptionError(Exception):
    """Raised when Whisper cannot transcribe the given audio."""


def transcribe(audio_path: Path, *, language: str = "ko") -> str:
    """Transcribe an audio file with OpenAI Whisper. Returns plain text."""
    if not audio_path.exists():
        raise TranscriptionError(f"오디오 파일을 찾을 수 없습니다: {audio_path}")
    size = audio_path.stat().st_size
    if size == 0:
        raise TranscriptionError("오디오 파일이 비어 있습니다.")
    if size > WHISPER_SIZE_LIMIT:
        raise TranscriptionError(
            f"오디오 파일이 Whisper 제한(25MB)을 초과했습니다: {size / 1024 / 1024:.1f}MB"
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise TranscriptionError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    client = OpenAI(api_key=api_key)
    try:
        with open(audio_path, "rb") as fh:
            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=fh,
                language=language or None,
                response_format="text",
            )
    except AuthenticationError as exc:
        raise TranscriptionError("OpenAI API 키가 유효하지 않습니다.") from exc
    except RateLimitError as exc:
        raise TranscriptionError("OpenAI API 호출량 제한에 걸렸습니다. 잠시 후 재시도하세요.") from exc
    except BadRequestError as exc:
        msg = str(exc).lower()
        if "maximum" in msg and ("size" in msg or "25" in msg):
            raise TranscriptionError("Whisper 파일 크기 제한(25MB)을 초과했습니다.") from exc
        raise TranscriptionError(f"Whisper 요청이 거부됐습니다: {exc}") from exc
    except APIConnectionError as exc:
        raise TranscriptionError("OpenAI 서버에 연결할 수 없습니다.") from exc
    except APIStatusError as exc:
        raise TranscriptionError(f"OpenAI API 오류({exc.status_code}): {exc.message}") from exc

    # response_format="text" returns a plain string in the SDK.
    text = response if isinstance(response, str) else getattr(response, "text", "")
    text = (text or "").strip()
    if not text:
        raise TranscriptionError("Whisper가 빈 결과를 반환했습니다. 음성이 없을 수 있습니다.")
    return text


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 2:
        print("usage: python -m src.transcriber <audio-file>")
        sys.exit(1)
    print(transcribe(Path(sys.argv[1])))
