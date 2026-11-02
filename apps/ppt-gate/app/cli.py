from __future__ import annotations

import argparse
import json
import sys

from . import gate_settings, plans
from .cards import create_batch
from .config import Settings
from .db import init_db


def _settings() -> Settings:
    return Settings()


def issue_cards(args: argparse.Namespace) -> int:
    settings = _settings()
    init_db(settings.database_path)
    plans.seed_plans(settings.database_path)
    gate_settings.seed_settings(settings.database_path, settings)
    plan = plans.get_plan_or_default(settings.database_path, args.plan)
    valid_days = args.valid_days or int(plan["valid_days"])
    batch_id, codes = create_batch(
        settings.database_path,
        name=args.name,
        plan=args.plan,
        count=args.count,
        valid_days=valid_days,
        pepper=settings.effective_card_pepper,
    )
    base = settings.public_base_url.rstrip("/")
    payload = {
        "batch_id": batch_id,
        "plan": args.plan,
        "valid_days": valid_days,
        "count": len(codes),
        "cards": [
            {"code": code, "redeem_url": f"{base}/r/{code}"} for code in codes
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(
        "注意：兑换码原文只在这里显示一次，请立即导入卡密仓库并妥善保存。",
        file=sys.stderr,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppt-gate")
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue", help="生成一批兑换码")
    issue.add_argument("--name", default="manual", help="批次名称")
    issue.add_argument("--plan", default="trial", help="套餐标识")
    issue.add_argument("--count", type=int, default=1, help="数量")
    issue.add_argument("--valid-days", type=int, default=None, help="有效天数")
    issue.set_defaults(func=issue_cards)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
