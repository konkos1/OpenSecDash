import asyncio
import threading

from app.services.event_broadcaster import EventBroadcaster


def test_event_broadcaster_serializes_concurrent_starts():
    first_poll_started = threading.Event()
    second_poll_started = threading.Event()
    release_poll = threading.Event()
    calls_lock = threading.Lock()
    calls = 0

    def poll_state() -> tuple[bool, int]:
        nonlocal calls
        with calls_lock:
            calls += 1
            current_call = calls
        first_poll_started.set()
        if current_call == 2:
            second_poll_started.set()
        if not release_poll.wait(timeout=1):
            raise RuntimeError("Timed out waiting to release broadcaster poll")
        return True, current_call

    async def scenario() -> None:
        broadcaster = EventBroadcaster(poll_state, interval_seconds=60)
        first_start = asyncio.create_task(broadcaster.start())
        assert await asyncio.to_thread(first_poll_started.wait, 1)
        second_start = asyncio.create_task(broadcaster.start())
        concurrent_second_poll = await asyncio.to_thread(second_poll_started.wait, 0.05)
        release_poll.set()
        await asyncio.gather(first_start, second_start)
        await broadcaster.stop()

        assert not concurrent_second_poll
        assert calls == 1

    asyncio.run(scenario())


def test_event_broadcaster_stop_waits_for_startup_poll():
    poll_started = threading.Event()
    release_poll = threading.Event()

    def poll_state() -> tuple[bool, int]:
        poll_started.set()
        if not release_poll.wait(timeout=1):
            raise RuntimeError("Timed out waiting to release broadcaster poll")
        return True, 1

    async def scenario() -> None:
        broadcaster = EventBroadcaster(poll_state, interval_seconds=60)
        start_task = asyncio.create_task(broadcaster.start())
        assert await asyncio.to_thread(poll_started.wait, 1)
        stop_task = asyncio.create_task(broadcaster.stop())
        await asyncio.sleep(0)
        release_poll.set()
        await asyncio.gather(start_task, stop_task)

        running_pollers = [
            task
            for task in asyncio.all_tasks()
            if task.get_name() == "event-broadcaster" and not task.done()
        ]
        assert running_pollers == []

    asyncio.run(scenario())


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
