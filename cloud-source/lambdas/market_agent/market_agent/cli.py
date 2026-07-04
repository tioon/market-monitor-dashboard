import argparse
import sys

from .collectors import collect_all
from .config import get_settings
from .evaluation import build_weekly_performance_report, save_performance_report
from .report import build_report
from .render import render_short_from_package_file
from .storage import save_report
from .youtube_upload import publish_short_from_package_file
from .youtube import build_youtube_short_package, save_youtube_package
from .telegram import check_connection, send_message


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create and optionally send a daily market briefing.")
    parser.add_argument("--send", action="store_true", help="send the briefing to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="print the briefing without sending")
    parser.add_argument("--evaluate", action="store_true", help="run the weekly performance evaluation")
    parser.add_argument("--shorts", action="store_true", help="generate a YouTube shorts manifest instead of a Telegram briefing")
    parser.add_argument(
        "--render-short",
        nargs="?",
        const="shorts/latest.json",
        default=None,
        help="render a shorts package JSON into mp4 on this machine (default: shorts/latest.json)",
    )
    parser.add_argument(
        "--publish-short",
        nargs="?",
        const="shorts/latest.json",
        default=None,
        help="render and upload a shorts package JSON to YouTube (default: shorts/latest.json)",
    )
    parser.add_argument("--persona", default=None, help="persona id for shorts generation")
    parser.add_argument("--news-limit", type=int, default=3, help="number of news items to use for shorts")
    parser.add_argument("--tts-voice", default=None, help="edge-tts voice or macOS say fallback voice")
    parser.add_argument("--tts-rate", default="+0%", help="tts speaking rate")
    parser.add_argument("--tts-volume", default="+0%", help="tts volume")
    parser.add_argument(
        "--youtube-client-secrets",
        default=None,
        help="path to Google OAuth client secrets JSON for YouTube upload",
    )
    parser.add_argument(
        "--youtube-token-file",
        default=None,
        help="path to stored OAuth token JSON for YouTube upload",
    )
    parser.add_argument(
        "--privacy-status",
        default=None,
        choices=["private", "unlisted", "public"],
        help="override YouTube privacy status",
    )
    parser.add_argument("--thumbnail-path", default=None, help="optional thumbnail image to upload")
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.publish_short:
        result = publish_short_from_package_file(
            package_path=args.publish_short,
            tts_voice=args.tts_voice or "ko-KR-SunHiNeural",
            tts_rate=args.tts_rate,
            tts_volume=args.tts_volume,
            client_secrets_file=args.youtube_client_secrets or settings.youtube_client_secrets_file,
            token_file=args.youtube_token_file or settings.youtube_token_file,
            privacy_status=args.privacy_status,
            thumbnail_path=args.thumbnail_path,
        )
        print(f"Rendered shorts video: {result['render']['output_path']}")
        print(f"Uploaded YouTube video: {result['upload']['video_url']}")
        return 0

    if args.render_short:
        package_path = args.render_short
        result = render_short_from_package_file(
            package_path=package_path,
            tts_voice=args.tts_voice or "ko-KR-SunHiNeural",
            tts_rate=args.tts_rate,
            tts_volume=args.tts_volume,
        )
        print(f"Rendered shorts video: {result['output_path']}")
        return 0

    if args.shorts:
        data = collect_all()
        package = build_youtube_short_package(
            data,
            persona_id=args.persona or settings.youtube_persona,
            news_limit=max(1, args.news_limit),
        )
        md_path, json_path = save_youtube_package(package)
        print(package["title"])
        print(f"\nSaved {md_path} and {json_path}", file=sys.stderr)
        if args.dry_run:
            print(package["script"]["tts_text"])
        return 0

    if args.evaluate:
        payload = build_weekly_performance_report(table_name=settings.history_table_name)
        md_path, json_path = save_performance_report(payload, table_name=settings.history_table_name)
        if args.send:
            try:
                check_connection(settings.telegram_bot_token, settings.telegram_chat_id)
                send_message(settings.telegram_bot_token, settings.telegram_chat_id, payload["report_text"])
            except Exception as exc:
                print(f"Telegram 연결이 끊겼습니다: {exc}", file=sys.stderr)
                return 2
            print(f"Sent weekly evaluation. Saved {md_path} and {json_path}")
        else:
            print(payload["report_text"])
            print(f"\nSaved {md_path} and {json_path}", file=sys.stderr)
            if not args.dry_run:
                print("Tip: use --send to deliver the weekly evaluation to Telegram.", file=sys.stderr)
        return 0

    data = collect_all()
    report, decision_snapshot = build_report(
        data,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        concise=True,
        return_snapshot=True,
    )
    md_path, json_path = save_report(
        data,
        report,
        history_table_name=settings.history_table_name,
        decision_table_name=settings.decision_table_name,
        decision_snapshot=decision_snapshot,
    )

    if args.send:
        try:
            check_connection(settings.telegram_bot_token, settings.telegram_chat_id)
            send_message(settings.telegram_bot_token, settings.telegram_chat_id, report)
        except Exception as exc:
            print(f"Telegram 연결이 끊겼습니다: {exc}", file=sys.stderr)
            return 2
        print(f"Sent Telegram briefing. Saved {md_path} and {json_path}")
    else:
        print(report)
        print(f"\nSaved {md_path} and {json_path}", file=sys.stderr)
        if not args.dry_run:
            print("Tip: use --send to deliver to Telegram.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
