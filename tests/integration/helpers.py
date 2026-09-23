from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from sqlalchemy import func, insert, select

from ticketflow.models import ReservationGroup, TicketReservation
from ticketflow.seed import demo_id


def race(*operations):
    barrier = Barrier(len(operations), timeout=10)

    def invoke(operation):
        barrier.wait()
        return operation()

    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [executor.submit(invoke, operation) for operation in operations]
        return [future.result(timeout=20) for future in futures]


def old_reservation(engine, seat_ids=None, expires_in=-1):
    event_id, user_id = demo_id("event/concert"), demo_id("customer")
    seat_ids = seat_ids or [demo_id("seat/VIP/1")]
    with engine.begin() as connection:
        now = connection.scalar(select(func.clock_timestamp()))
        expiry = now + timedelta(seconds=expires_in)
        group = connection.scalar(
            insert(ReservationGroup)
            .values(
                user_id=user_id,
                event_id=event_id,
                created_at=now - timedelta(minutes=10),
                expires_at=expiry,
            )
            .returning(ReservationGroup.id)
        )
        for seat in seat_ids:
            connection.execute(
                insert(TicketReservation).values(
                    group_id=group,
                    user_id=user_id,
                    event_id=event_id,
                    seat_id=seat,
                    expires_at=expiry,
                )
            )
    return group
