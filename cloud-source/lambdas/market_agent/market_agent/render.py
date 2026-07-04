import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .tts import DEFAULT_KO_VOICE, synthesize_tts


DEFAULT_RENDER_SIZE = "1080x1920"
DEFAULT_FPS = 30
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_VIDEO_CODEC = "libx264"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


def _escape_ass_text(text: str) -> str:
    return (
        str(text)
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def build_ass_subtitles(package: Dict[str, Any]) -> str:
    title = _escape_ass_text(str(package.get("title", "시장 쇼츠")).strip())
    scenes = package.get("script", {}).get("scenes", [])
    total_duration = sum(float(scene.get("duration_sec", 0) or 0) for scene in scenes) or 1.0
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Title,Noto Sans CJK KR,46,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,2,0,8,80,80,60,1",
        "Style: Scene,Noto Sans CJK KR,44,&H00FFFFFF,&H000000FF,&H96000000,&H78000000,-1,0,0,0,100,100,0,0,1,2,0,2,80,80,120,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,0:00:00.00,{_ass_time(total_duration)},Title,,0,0,0,,{title}",
    ]
    start = 0.0
    for scene in scenes:
        duration = float(scene.get("duration_sec", 0) or 0)
        end = start + duration
        screen_text = _escape_ass_text(str(scene.get("screen_text", "")).strip())
        narration = _escape_ass_text(str(scene.get("narration", "")).strip())
        text = screen_text or narration
        if screen_text and narration:
            text = f"{screen_text}\\N{narration}"
        elif not text:
            text = " "
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Scene,,0,0,0,,{text}")
        start = end
    return "\n".join(lines) + "\n"


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required but not installed")


def _write_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _safe_font_paths() -> List[Path]:
    candidates = [
        Path("/System/Library/Fonts/Apple SD Gothic Neo.ttc"),
        Path("/System/Library/Fonts/AppleGothic.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    ]
    return [path for path in candidates if path.exists()]


def _build_poster_image(package: Dict[str, Any], output_path: Path) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - handled during local setup
        raise RuntimeError("Pillow is required to build poster-style shorts frames") from exc

    width, height = 1080, 1920
    bg_top = (7, 17, 31)
    bg_bottom = (17, 28, 44)
    image = Image.new("RGB", (width, height), bg_top)
    draw = ImageDraw.Draw(image)

    def blend(c1, c2, t):
        return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    for y in range(height):
        ratio = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=blend(bg_top, bg_bottom, ratio))

    font_paths = _safe_font_paths()

    def load_font(size: int, bold: bool = False):
        if font_paths:
            return ImageFont.truetype(str(font_paths[0]), size=size)
        return ImageFont.load_default()

    title_font = load_font(66)
    body_font = load_font(42)
    small_font = load_font(32)
    tiny_font = load_font(28)

    def wrap_text(text: str, font, max_width: int) -> List[str]:
        words = str(text).split()
        if not words:
            return [""]
        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = current + " " + word
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    margin = 72
    y = 88

    title = str(package.get("title", "시장 브리핑 쇼츠"))
    for line in wrap_text(title, title_font, width - margin * 2):
        draw.text((margin, y), line, font=title_font, fill=(255, 255, 255))
        y += 76

    y += 24
    market = package.get("market_context", {})
    summary = f"{market.get('regime', 'n/a')} · {market.get('plain_verdict', 'n/a')} · score {market.get('score', 'n/a')}"
    draw.rounded_rectangle((margin, y, width - margin, y + 108), radius=24, fill=(255, 196, 0))
    draw.text((margin + 28, y + 28), summary, font=body_font, fill=(7, 17, 31))
    y += 150

    hook = str(package.get("script", {}).get("hook", "")).strip()
    draw.text((margin, y), "HOOK", font=small_font, fill=(255, 196, 0))
    y += 54
    for line in wrap_text(hook, body_font, width - margin * 2):
        draw.text((margin, y), line, font=body_font, fill=(255, 255, 255))
        y += 58

    y += 30
    draw.text((margin, y), "TODAY'S NEWS", font=small_font, fill=(255, 196, 0))
    y += 52
    for index, item in enumerate(package.get("selected_news", [])[:3], start=1):
        headline = str(item.get("title", "")).strip()
        source = str(item.get("source", "")).strip()
        draw.rounded_rectangle((margin, y, width - margin, y + 214), radius=28, fill=(18, 31, 49))
        draw.text((margin + 24, y + 22), f"{index}. {source}", font=tiny_font, fill=(135, 160, 190))
        yy = y + 66
        for line in wrap_text(headline, body_font, width - margin * 2 - 48):
            draw.text((margin + 24, yy), line, font=body_font, fill=(255, 255, 255))
            yy += 56
        y += 236

    footer = package.get("script", {}).get("tts_text", "")
    footer = footer[:180] + ("..." if len(footer) > 180 else "")
    draw.text((margin, height - 220), footer, font=tiny_font, fill=(200, 208, 220))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def render_short_video(
    package: Dict[str, Any],
    output_path: Path,
    tts_path: Optional[Path] = None,
    tts_voice: str = DEFAULT_KO_VOICE,
    tts_rate: str = "+0%",
    tts_volume: str = "+0%",
) -> Dict[str, Any]:
    _require_tool("ffmpeg")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        poster_path = _build_poster_image(package, tmpdir_path / "poster.png")
        if tts_path is None:
            tts_path = tmpdir_path / "narration.mp3"
            tts_result = synthesize_tts(
                package.get("script", {}).get("tts_text", ""),
                tts_path,
                voice=tts_voice,
                rate=tts_rate,
                volume=tts_volume,
            )
        else:
            tts_result = {"engine": "external", "output_path": str(tts_path)}

        total_duration = sum(float(scene.get("duration_sec", 0) or 0) for scene in package.get("script", {}).get("scenes", [])) or 1.0
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(poster_path),
            "-i",
            str(tts_path),
            "-c:v",
            DEFAULT_VIDEO_CODEC,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            DEFAULT_AUDIO_CODEC,
            "-shortest",
            str(output_path),
        ]
        subprocess.run(
            ffmpeg_cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return {
        "output_path": str(output_path),
        "tts": tts_result,
        "duration_sec": total_duration,
    }


def render_short_from_package_file(
    package_path: Path,
    output_path: Optional[Path] = None,
    tts_voice: str = DEFAULT_KO_VOICE,
    tts_rate: str = "+0%",
    tts_volume: str = "+0%",
) -> Dict[str, Any]:
    package_path = Path(package_path)
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if output_path is None:
        output_path = package_path.with_suffix(".mp4")
    return render_short_video(
        package,
        output_path=Path(output_path),
        tts_voice=tts_voice,
        tts_rate=tts_rate,
        tts_volume=tts_volume,
    )
