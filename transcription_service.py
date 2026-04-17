import os
from dataclasses import dataclass
from typing import Optional, Tuple

from app_logger import get_logger


logger = get_logger()


@dataclass
class TranscriptionConfig:
    enabled: bool = True
    model_size: str = "small"
    model_path: str = ""
    language: str = ""
    compute_type: str = "int8"
    local_files_only: bool = True


class TranscriptionService:
    def transcribe_file(
        self,
        audio_path: str,
        transcript_path: str,
        config: TranscriptionConfig,
    ) -> Tuple[bool, Optional[str]]:
        raise NotImplementedError


class FasterWhisperService(TranscriptionService):
    def transcribe_file(
        self,
        audio_path: str,
        transcript_path: str,
        config: TranscriptionConfig,
    ) -> Tuple[bool, Optional[str]]:
        if not config.enabled:
            return True, None

        if not os.path.exists(audio_path):
            return False, "Audio file for transcription was not found."

        model_ref = config.model_path.strip() or config.model_size
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            logger.exception("Failed to import faster-whisper.")
            return False, (
                "faster-whisper is not installed or failed to load. "
                f"Details: {exc}"
            )

        try:
            logger.info(
                "Starting local transcription | model=%s | compute_type=%s | local_only=%s",
                model_ref,
                config.compute_type,
                config.local_files_only,
            )
            model = WhisperModel(
                model_ref,
                device="auto",
                compute_type=config.compute_type,
                local_files_only=config.local_files_only,
            )

            language = config.language.strip() or None
            segments, info = model.transcribe(
                audio_path,
                language=language,
                vad_filter=True,
            )
            lines = []
            for segment in segments:
                text = segment.text.strip()
                if text:
                    lines.append(text)

            content = "\n".join(lines).strip()
            if not content:
                content = "[No speech detected]"

            with open(transcript_path, "w", encoding="utf-8") as txt_file:
                txt_file.write(content)

            logger.info(
                "Transcription completed | language=%s | duration=%.2f",
                getattr(info, "language", "unknown"),
                getattr(info, "duration", 0.0),
            )
            return True, None
        except Exception as exc:
            logger.exception("Transcription failed.")
            return False, str(exc)
