import asyncio
import threading

from app.services.event_broadcaster import EventBroadcaster


def test_event_broadcaster_poll_rate_does_not_grow_with_subscribers():
    lock = threading.Lock()
    calls = 0

    def poll_state() -> tuple[bool, int]:
        nonlocal calls
        with lock:
            calls += 1
            return True, calls

    async def scenario() -> None:
        broadcaster = EventBroadcaster(poll_state, interval_seconds=0.01)
        subscribers = [broadcaster.subscribe() for _ in range(50)]
        await broadcaster.start()
        await asyncio.sleep(0.055)
        await broadcaster.stop()

        assert 3 <= calls <= 10
        assert all(queue.qsize() == 1 for queue in subscribers)
        latest_ids = [queue.get_nowait().last_event_id for queue in subscribers]
        assert len(set(latest_ids)) == 1

    asyncio.run(scenario())


def test_event_broadcaster_keeps_only_latest_notification_for_slow_clients():
    next_id = 0

    def poll_state() -> tuple[bool, int]:
        nonlocal next_id
        next_id += 1
        return True, next_id

    async def scenario() -> None:
        broadcaster = EventBroadcaster(poll_state, interval_seconds=0.005)
        subscriber = broadcaster.subscribe()
        await broadcaster.start()
        await asyncio.sleep(0.04)
        await broadcaster.stop()

        assert subscriber.qsize() == 1
        latest_notified_id = subscriber.get_nowait().last_event_id
        assert 0 <= next_id - latest_notified_id <= 1

    asyncio.run(scenario())
