"""Periodic reservation expiry; run alongside the API."""

import argparse
import logging
import signal
from threading import Event

from ticketflow.config import Settings
from ticketflow.database import build_engine
from ticketflow.reservations import expire_reservations


def main():
    parser = argparse.ArgumentParser(description="Expire TicketFlow reservations")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be positive")
    logging.basicConfig(level=logging.INFO)
    stopped = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    engine = build_engine(Settings())
    try:
        while not stopped.is_set():
            try:
                count = expire_reservations(engine)
                logging.info("Expired reservations: %s", count)
            except Exception:
                logging.exception("Reservation expiry failed")
                if args.once:
                    raise
            if args.once:
                break
            stopped.wait(args.interval)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
