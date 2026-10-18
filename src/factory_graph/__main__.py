"""Separate-process demo and procurement worker: python -m factory_graph."""

import argparse
import json
import uuid

from .seed import seed
from .store import Store
from .workflow import procurement_handoff, run


def main():
    parser = argparse.ArgumentParser(description="Factory Graph local operations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    sub.add_parser("board")
    procurement = sub.add_parser("procurement")
    procurement.add_argument("--worker", default="procurement-cli")
    start = sub.add_parser("start")
    start.add_argument("--machine", default="M1")
    start.add_argument("--day", type=int, default=0)
    start.add_argument("--until", type=int, default=0)
    decision = sub.add_parser("decide")
    decision.add_argument("thread")
    decision.add_argument("hash")
    decision.add_argument("--approve", action="store_true")
    args = parser.parse_args()
    store = Store()
    if args.command == "seed":
        result = {"created": seed(store)}
    elif args.command == "board":
        result = store.board()
    elif args.command == "procurement":
        result = procurement_handoff(store, args.worker)
    elif args.command == "start":
        if not 0 <= args.day <= args.until <= 29:
            parser.error("Use an outage within days 0 through 29")
        seed(store)
        result = run(
            store,
            str(uuid.uuid4()),
            initial={"machine": args.machine, "day": args.day, "until": args.until},
        )
    else:
        result = run(store, args.thread, decision={"hash": args.hash, "approve": args.approve})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
