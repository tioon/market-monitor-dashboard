import argparse
import sys

from .collectors import collect_crypto_all
from .config import get_settings
from .evaluation import build_weekly_performance_report, save_performance_report
from .report import build_crypto_report
from .storage import save_report
from .telegram import send_message


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create and optionally send a daily crypto market briefing.")
    parser.add_argument("--send", action="store_true", help="send the briefing to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="print the briefing without sending")
    parser.add_argument("--evaluate", action="store_true", help="run the weekly performance evaluation")
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.evaluate:
        payload = build_weekly_performance_report(table_name=settings.history_table_name)
        md_path, json_path = save_performance_report(payload, table_name=settings.history_table_name)
        if args.send:
            send_message(settings.telegram_bot_token, settings.telegram_chat_id, payload["report_text"])
            print(f"Sent weekly evaluation. Saved {md_path} and {json_path}")
        else:
            print(payload["report_text"])
            print(f"\nSaved {md_path} and {json_path}", file=sys.stderr)
            if not args.dry_run:
                print("Tip: use --send to deliver the weekly evaluation to Telegram.", file=sys.stderr)
        return 0

    data = collect_crypto_all()
    report, decision_snapshot = build_crypto_report(
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
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, report)
        print(f"Sent Telegram crypto briefing. Saved {md_path} and {json_path}")
    else:
        print(report)
        print(f"\nSaved {md_path} and {json_path}", file=sys.stderr)
        if not args.dry_run:
            print("Tip: use --send to deliver to Telegram.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
