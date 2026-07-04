import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .config import DEFAULT_YOUTUBE_CLIENT_SECRETS_FILE, DEFAULT_YOUTUBE_TOKEN_FILE
from .render import render_short_from_package_file


YOUTUBE_UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _require_google_upload_deps() -> Dict[str, Any]:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:  # pragma: no cover - exercised via CLI error path
        raise RuntimeError(
            "Google upload dependencies are missing. Install google-api-python-client, "
            "google-auth, google-auth-oauthlib, and google-auth-httplib2 first."
        ) from exc

    return {
        "build": build,
        "MediaFileUpload": MediaFileUpload,
        "InstalledAppFlow": InstalledAppFlow,
        "Request": Request,
        "Credentials": Credentials,
    }


def _load_client_secrets_path(client_secrets_file: Optional[str]) -> Path:
    path = Path(client_secrets_file or DEFAULT_YOUTUBE_CLIENT_SECRETS_FILE)
    if not path.exists():
        raise FileNotFoundError(
            f"YouTube OAuth client secret file not found: {path}. "
            "Download an OAuth client JSON from Google Cloud Console and place it there, "
            "or pass --youtube-client-secrets."
        )
    return path


def _load_token_path(token_file: Optional[str]) -> Path:
    path = Path(token_file or DEFAULT_YOUTUBE_TOKEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_youtube_credentials(
    client_secrets_file: Optional[str] = None,
    token_file: Optional[str] = None,
) -> Any:
    deps = _require_google_upload_deps()
    client_secrets_path = _load_client_secrets_path(client_secrets_file)
    token_path = _load_token_path(token_file)
    Credentials = deps["Credentials"]
    Request = deps["Request"]
    InstalledAppFlow = deps["InstalledAppFlow"]

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_UPLOAD_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), YOUTUBE_UPLOAD_SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def build_youtube_upload_body(package: Dict[str, Any], privacy_status: Optional[str] = None) -> Dict[str, Any]:
    upload_meta = package.get("upload", {})
    title = str(package.get("title", "Market Agent Shorts")).strip()
    description = str(package.get("description", "")).strip()
    tags = list(package.get("tags", []))
    if privacy_status is None:
        privacy_status = str(upload_meta.get("privacy_status", "private"))
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(upload_meta.get("category_id", "25")),
            "defaultLanguage": str(upload_meta.get("language", "ko")),
            "defaultAudioLanguage": str(upload_meta.get("language", "ko")),
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(upload_meta.get("made_for_kids", False)),
        },
    }
    return body


def upload_youtube_video(
    video_path: Union[str, Path],
    package: Dict[str, Any],
    client_secrets_file: Optional[str] = None,
    token_file: Optional[str] = None,
    privacy_status: Optional[str] = None,
    thumbnail_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    deps = _require_google_upload_deps()
    build = deps["build"]
    MediaFileUpload = deps["MediaFileUpload"]

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    creds = load_youtube_credentials(
        client_secrets_file=client_secrets_file,
        token_file=token_file,
    )
    service = build("youtube", "v3", credentials=creds)
    body = build_youtube_upload_body(package, privacy_status=privacy_status)

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response: Dict[str, Any] = {}
    while response is None or "id" not in response:
        _, response = request.next_chunk()

    video_id = str(response["id"])
    result = {
        "video_id": video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "privacy_status": body["status"]["privacyStatus"],
        "video_path": str(video_path),
        "token_file": str(_load_token_path(token_file)),
        "client_secrets_file": str(_load_client_secrets_path(client_secrets_file)),
        "title": body["snippet"]["title"],
    }

    if thumbnail_path is not None:
        thumb_path = Path(thumbnail_path)
        if not thumb_path.exists():
            raise FileNotFoundError(f"Thumbnail file not found: {thumb_path}")
        thumb_media = MediaFileUpload(str(thumb_path), chunksize=-1, resumable=False)
        service.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
        result["thumbnail_path"] = str(thumb_path)

    return result


def publish_short_from_package_file(
    package_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    tts_voice: str = "ko-KR-SunHiNeural",
    tts_rate: str = "+0%",
    tts_volume: str = "+0%",
    client_secrets_file: Optional[str] = None,
    token_file: Optional[str] = None,
    privacy_status: Optional[str] = None,
    thumbnail_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    package_path = Path(package_path)
    render_result = render_short_from_package_file(
        package_path=package_path,
        output_path=Path(output_path) if output_path is not None else None,
        tts_voice=tts_voice,
        tts_rate=tts_rate,
        tts_volume=tts_volume,
    )
    package = json.loads(package_path.read_text(encoding="utf-8"))
    upload_result = upload_youtube_video(
        render_result["output_path"],
        package,
        client_secrets_file=client_secrets_file,
        token_file=token_file,
        privacy_status=privacy_status,
        thumbnail_path=thumbnail_path,
    )
    return {
        "package_path": str(package_path),
        "render": render_result,
        "upload": upload_result,
    }
