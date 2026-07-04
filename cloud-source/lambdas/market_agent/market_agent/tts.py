import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_KO_VOICE = "ko-KR-SunHiNeural"


def _have_edge_tts() -> bool:
    try:
        import edge_tts  # noqa: F401
    except Exception:
        return False
    return True


async def _edge_tts_save(text: str, output_path: Path, voice: str, rate: str, volume: str) -> None:
    import edge_tts

    communicator = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicator.save(str(output_path))


def _mac_say_save(text: str, output_path: Path, voice: Optional[str] = None, rate: Optional[int] = None) -> None:
    if shutil.which("say") is None:
        raise RuntimeError("macOS 'say' command not found")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_aiff = Path(tmpdir) / "tts.aiff"
        cmd = ["say", "-o", str(tmp_aiff)]
        if voice:
            cmd.extend(["-v", voice])
        if rate:
            cmd.extend(["-r", str(rate)])
        cmd.append(text)
        subprocess.run(cmd, check=True)

        if shutil.which("ffmpeg") is None:
            if output_path.suffix.lower() == ".aiff":
                output_path.write_bytes(tmp_aiff.read_bytes())
                return
            raise RuntimeError("ffmpeg is required to convert macOS say output")

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp_aiff),
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def synthesize_tts(
    text: str,
    output_path: Path,
    voice: str = DEFAULT_KO_VOICE,
    rate: str = "+0%",
    volume: str = "+0%",
    fallback_voice: Optional[str] = "Yuna",
) -> Dict[str, Any]:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if _have_edge_tts():
        try:
            asyncio.run(_edge_tts_save(text, output_path, voice=voice, rate=rate, volume=volume))
            return {
                "engine": "edge-tts",
                "voice": voice,
                "rate": rate,
                "volume": volume,
                "output_path": str(output_path),
            }
        except Exception as exc:
            edge_error = str(exc)
    else:
        edge_error = "edge-tts unavailable"

    try:
        rate_value = None
        if isinstance(rate, str) and rate.endswith("%"):
            try:
                rate_value = int(float(rate.replace("%", "")))
            except ValueError:
                rate_value = None
        _mac_say_save(text, output_path, voice=fallback_voice, rate=rate_value)
        return {
            "engine": "mac-say",
            "voice": fallback_voice,
            "rate": rate,
            "volume": volume,
            "fallback_reason": edge_error,
            "output_path": str(output_path),
        }
    except Exception as exc:
        raise RuntimeError(f"TTS failed: {edge_error}; fallback failed: {exc}") from exc
