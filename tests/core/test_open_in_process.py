import asyncio
import pickle
import signal

import pytest

from asyncio_run_in_process import (
    ProcessKilled,
    constants,
    open_in_process,
)
from asyncio_run_in_process.exceptions import (
    ChildCancelled,
)


@pytest.mark.asyncio
async def test_open_in_proc_SIGTERM_while_running():
    async def do_sleep_forever():
        while True:
            await asyncio.sleep(0)

    async with open_in_process(do_sleep_forever) as proc:
        proc.terminate()
    assert proc.returncode == 15


@pytest.mark.asyncio
async def test_open_in_proc_SIGKILL_while_running():
    async def do_sleep_forever():
        while True:
            await asyncio.sleep(0)

    async with open_in_process(do_sleep_forever) as proc:
        await proc.kill()
    assert proc.returncode == -9
    assert isinstance(proc.error, ProcessKilled)


@pytest.mark.asyncio
async def test_open_proc_SIGINT_while_running():
    async def do_sleep_forever():
        while True:
            await asyncio.sleep(0)

    async with open_in_process(do_sleep_forever) as proc:
        proc.send_signal(signal.SIGINT)
    assert proc.returncode == 2


@pytest.mark.asyncio
async def test_open_proc_SIGINT_can_be_handled():
    async def do_sleep_forever():
        try:
            while True:
                await asyncio.sleep(0)
        except KeyboardInterrupt:
            return 9999

    async with open_in_process(do_sleep_forever) as proc:
        proc.send_signal(signal.SIGINT)
    assert proc.returncode == 0
    assert proc.get_result_or_raise() == 9999


@pytest.mark.asyncio
async def test_open_proc_SIGINT_can_be_ignored():
    async def do_sleep_forever():
        try:
            while True:
                await asyncio.sleep(0)
        except KeyboardInterrupt:
            # silence the first SIGINT
            pass

        try:
            while True:
                await asyncio.sleep(0)
        except KeyboardInterrupt:
            return 9999

    async with open_in_process(do_sleep_forever) as proc:
        proc.send_signal(signal.SIGINT)
        await asyncio.sleep(0.01)
        proc.send_signal(signal.SIGINT)

    assert proc.returncode == 0
    assert proc.get_result_or_raise() == 9999


@pytest.mark.asyncio
async def test_open_proc_invalid_function_call():
    async def takes_no_args():
        pass

    async with open_in_process(takes_no_args, 1, 2, 3) as proc:
        pass
    assert proc.returncode == 1
    assert isinstance(proc.error, TypeError)


@pytest.mark.asyncio
async def test_open_proc_unpickleable_params(touch_path):
    async def takes_open_file(f):
        pass

    with pytest.raises(pickle.PickleError):
        with open(touch_path, "w") as touch_file:
            async with open_in_process(takes_open_file, touch_file):
                # this code block shouldn't get executed
                assert False


@pytest.mark.asyncio
async def test_open_proc_KeyboardInterrupt_while_running():
    async def do_sleep_forever():
        while True:
            await asyncio.sleep(0)

    with pytest.raises(KeyboardInterrupt):
        async with open_in_process(do_sleep_forever) as proc:
            raise KeyboardInterrupt
    assert proc.returncode == 2


class CustomException(BaseException):
    pass


@pytest.mark.asyncio
async def test_open_proc_does_not_hang_on_exception():
    async def do_sleep_forever():
        while True:
            await asyncio.sleep(0)

    async def _do_inner():
        with pytest.raises(CustomException):
            async with open_in_process(do_sleep_forever):
                raise CustomException("Just a boring exception")

    await asyncio.wait_for(_do_inner(), timeout=1)


@pytest.mark.asyncio
async def test_cancelled_error_in_child():
    # An asyncio.CancelledError from the child process will be converted into a ChildCancelled.
    async def raise_err():
        await asyncio.sleep(0.01)
        raise asyncio.CancelledError()

    async def _do_inner():
        async with open_in_process(raise_err) as proc:
            await proc.wait_result_or_raise()

    with pytest.raises(ChildCancelled):
        await asyncio.wait_for(_do_inner(), timeout=1)


@pytest.mark.asyncio
async def test_task_cancellation(monkeypatch):
    # If the task executing open_in_process() is cancelled, we will ask the child proc to
    # terminate and propagate the CancelledError.

    async def store_received_signals():
        # Return only when we receive a SIGTERM, also checking that we received a SIGINT before
        # the SIGTERM.
        received_signals = []
        loop = asyncio.get_event_loop()
        for sig in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(sig, received_signals.append, sig)
        while True:
            if signal.SIGTERM in received_signals:
                assert [signal.SIGINT, signal.SIGTERM] == received_signals
                return
            await asyncio.sleep(0)

    child_started = asyncio.Event()

    async def runner():
        async with open_in_process(store_received_signals) as proc:
            child_started.set()
            await proc.wait_result_or_raise()

    monkeypatch.setattr(constants, 'SIGINT_TIMEOUT_SECONDS', 0.2)
    monkeypatch.setattr(constants, 'SIGTERM_TIMEOUT_SECONDS', 0.2)
    task = asyncio.ensure_future(runner())
    await asyncio.wait_for(child_started.wait(), timeout=1)
    assert not task.done()
    task.cancel()
    # For some reason, using pytest.raises() here doesn't seem to prevent the
    # asyncio.CancelledError from closing the event loop, causing subsequent tests to fail.
    raised_cancelled_error = False
    try:
        await asyncio.wait_for(task, timeout=1)
    except asyncio.CancelledError:
        raised_cancelled_error = True
    assert raised_cancelled_error
