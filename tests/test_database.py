"""Database layer: migrations, reminder leasing and safe concurrent use of one SQLite connection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from enan_ultra.storage.database import Database

KEY = "k" * 48


def run_with_database(tmp_path, scenario, **options):
    async def main():
        database = Database(tmp_path / "test.sqlite3", data_key=KEY, **options)
        await database.open()
        try:
            return await scenario(database)
        finally:
            await database.close()

    return asyncio.run(main())


async def scalar(database, sql, params=()):
    cursor = await database._db().execute(sql, params)
    row = await cursor.fetchone()
    await cursor.close()
    return row[0]


def test_open_is_idempotent_and_reports_healthy(tmp_path):
    async def first(database):
        assert await database.health_check()
        return await scalar(database, "SELECT MAX(version) FROM schema_version")

    version = run_with_database(tmp_path, first)
    assert version and version > 0

    async def second(database):
        count = await scalar(database, "SELECT COUNT(*) FROM schema_version")
        return await scalar(database, "SELECT MAX(version) FROM schema_version"), count

    assert run_with_database(tmp_path, second)[0] == version


def test_concurrent_transactional_writers_all_succeed(tmp_path):
    """Regression: 19 of 20 simultaneous add_task calls used to fail with
    'cannot start a transaction within a transaction' on the shared connection."""

    async def scenario(database):
        results = await asyncio.gather(
            *[database.add_task(1000 + index, f"task {index}") for index in range(20)], return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        assert errors == []
        assert await scalar(database, "SELECT COUNT(*) FROM user_tasks") == 20
        assert not database._db().in_transaction

    run_with_database(tmp_path, scenario)


def test_failed_transactions_do_not_leak_or_disturb_other_writers(tmp_path):
    async def scenario(database):
        async def good(index):
            await database.add_task(2000 + index, f"task {index}")

        async def bad(index):
            with pytest.raises(ValueError):
                await database.add_task(3000 + index, "needs a missing prerequisite", depends_on=[999999])

        async def plain(index):
            await database.save_message(500 + index, "user", f"message {index}")

        jobs = []
        for index in range(30):
            jobs += [good(index), bad(index), plain(index)]
        await asyncio.gather(*jobs)
        assert await scalar(database, "SELECT COUNT(*) FROM user_tasks WHERE user_id BETWEEN 2000 AND 2999") == 30
        assert await scalar(database, "SELECT COUNT(*) FROM user_tasks WHERE user_id >= 3000") == 0
        assert await scalar(database, "SELECT COUNT(*) FROM messages") == 30
        assert not database._db().in_transaction

    run_with_database(tmp_path, scenario)


def test_cancelled_operation_does_not_poison_the_connection(tmp_path):
    async def scenario(database):
        task = asyncio.create_task(database.add_task(9, "cancel me"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not database._db().in_transaction
        await database.add_task(10, "the connection still works")
        assert await scalar(database, "SELECT COUNT(*) FROM user_tasks WHERE user_id=10") == 1

    run_with_database(tmp_path, scenario)


def test_public_methods_can_call_each_other_without_deadlock(tmp_path):
    async def scenario(database):
        # set_global_access_mode() writes an audit event through another public method.
        await asyncio.wait_for(database.set_global_access_mode("public", 1), timeout=5)
        assert await database.get_global_access_mode() == "public"
        assert (await database.verify_audit_chain())["valid"] is True

    run_with_database(tmp_path, scenario)


def test_history_round_trip_keeps_order_and_limit(tmp_path):
    async def scenario(database):
        for index in range(15):
            await database.save_message(77, "user" if index % 2 == 0 else "assistant", f"m{index}", keep=10)
        history = await database.history(77, 50)
        assert [item["content"] for item in history] == [f"m{index}" for index in range(5, 15)]

    run_with_database(tmp_path, scenario)


def test_reminder_lease_defer_and_delivery(tmp_path):
    async def scenario(database):
        due = datetime.now(UTC) - timedelta(minutes=1)
        reminder_id = await database.add_reminder(5, 5, "stand up", due)
        claimed = await database.claim_due_reminders()
        assert [item["id"] for item in claimed] == [reminder_id]
        assert await database.claim_due_reminders() == [], "a leased reminder must not be claimed twice"
        assert await database.defer_reminder(reminder_id, 60)
        await database.mark_reminder_delivered(reminder_id)
        assert await database.claim_due_reminders() == []

    run_with_database(tmp_path, scenario)


def test_reminder_limit_is_enforced(tmp_path):
    async def scenario(database):
        later = datetime.now(UTC) + timedelta(days=1)
        await database.add_reminder(5, 5, "one", later)
        await database.add_reminder(5, 5, "two", later)
        with pytest.raises(ValueError):
            await database.add_reminder(5, 5, "three", later)

    run_with_database(tmp_path, scenario, max_pending_reminders=2)


def test_deleting_user_data_keeps_the_audit_chain_verifiable(tmp_path):
    async def scenario(database):
        await database.save_message(41, "user", "private text")
        await database.save_message(42, "user", "other user")
        await database.audit(41, "test_action", "target", "ok")
        await database.audit(42, "test_action", "target", "ok")
        # Private-chat messages are removed only when the private chat id is passed explicitly.
        await database.delete_user_data(41, 41)
        assert await database.history(41, 10) == []
        assert len(await database.history(42, 10)) == 1
        assert (await database.verify_audit_chain())["valid"] is True

    run_with_database(tmp_path, scenario)
