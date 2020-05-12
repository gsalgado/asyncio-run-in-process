import asyncio
import pickle
import signal

import pytest
import trio

from asyncio_run_in_process import (
    ProcessKilled,
    constants,
    open_in_process,
    open_in_process_with_trio,
)
from asyncio_run_in_process.exceptions import (
    ChildCancelled,
)


@pytest.fixture(params=('use_trio', 'use_asyncio'))
def open_in_proc_and_sleep_fn(request):
    if request.param == 'use_trio':
        return open_in_process_with_trio, trio.sleep
    else:
        return open_in_process, asyncio.sleep


@pytest.mark.asyncio
async def test_SIGINT_on_method_using_run_in_executor():
    # This test exists only to show that one needs to be carefull when using run_in_executor() as
    # asyncio does not cancel the thread/process it starts, so we need to make sure they return or
    # else open_in_process() hangs forever. In the code below, this is achieved by setting the
    # stop_loop event before the method passed to open_in_process() returns. If we don't set that
    # event, the test hangs forever.
    async def loop_forever_in_executor():
        import threading
        stop_loop = threading.Event()

        def thread_loop():
            import time
            while not stop_loop.is_set():
                time.sleep(0.01)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, thread_loop)
        finally:
            stop_loop.set()

    async with open_in_process(loop_forever_in_executor) as proc:
        proc.send_signal(signal.SIGINT)
    assert proc.returncode == 2


@pytest.mark.asyncio
async def test_open_in_proc_SIGTERM_while_running(open_in_proc_and_sleep_fn):
    open_in_proc, sleep_fn = open_in_proc_and_sleep_fn

    async def do_sleep_forever():
        while True:
            await sleep_fn(0)

    async with open_in_proc(do_sleep_forever) as proc:
        proc.terminate()
    assert proc.returncode == 15


@pytest.mark.asyncio
async def test_open_in_proc_SIGKILL_while_running(open_in_proc_and_sleep_fn):
    open_in_proc, sleep_fn = open_in_proc_and_sleep_fn

    async def do_sleep_forever():
        while True:
            await sleep_fn(0)

    async with open_in_proc(do_sleep_forever) as proc:
        await proc.kill()
    assert proc.returncode == -9
    assert isinstance(proc.error, ProcessKilled)


@pytest.mark.asyncio
async def test_open_proc_SIGINT_while_running(open_in_proc_and_sleep_fn):
    open_in_proc, sleep_fn = open_in_proc_and_sleep_fn

    async def do_sleep_forever():
        while True:
            await sleep_fn(0)

    async with open_in_proc(do_sleep_forever) as proc:
        proc.send_signal(signal.SIGINT)
    assert proc.returncode == 2


@pytest.mark.asyncio
async def test_open_proc_SIGINT_can_be_handled(open_in_proc_and_sleep_fn):
    open_in_proc, sleep_fn = open_in_proc_and_sleep_fn

    async def do_sleep_forever():
        try:
            while True:
                await sleep_fn(0)
        except KeyboardInterrupt:
            return 9999

    async with open_in_proc(do_sleep_forever) as proc:
        proc.send_signal(signal.SIGINT)
    assert proc.returncode == 0
    assert proc.get_result_or_raise() == 9999


@pytest.mark.asyncio
async def test_open_proc_SIGINT_can_be_ignored(open_in_proc_and_sleep_fn):
    open_in_proc, sleep_fn = open_in_proc_and_sleep_fn

    async def do_sleep_forever():
        try:
            while True:
                await sleep_fn(0)
        except KeyboardInterrupt:
            # silence the first SIGINT
            pass

        try:
            while True:
                await sleep_fn(0)
        except KeyboardInterrupt:
            return 9999

    async with open_in_proc(do_sleep_forever) as proc:
        proc.send_signal(signal.SIGINT)
        await asyncio.sleep(0.01)
        proc.send_signal(signal.SIGINT)

    assert proc.returncode == 0
    assert proc.get_result_or_raise() == 9999


@pytest.mark.asyncio
async def test_open_proc_invalid_function_call(open_in_proc_and_sleep_fn):
    open_in_proc, _ = open_in_proc_and_sleep_fn

    async def takes_no_args():
        pass

    async with open_in_proc(takes_no_args, 1, 2, 3) as proc:
        pass
    assert proc.returncode == 1
    assert isinstance(proc.error, TypeError)


@pytest.mark.asyncio
async def test_open_proc_unpickleable_params(touch_path, open_in_proc_and_sleep_fn):
    open_in_proc, _ = open_in_proc_and_sleep_fn

    async def takes_open_file(f):
        pass

    with pytest.raises(pickle.PickleError):
        with open(touch_path, "w") as touch_file:
            async with open_in_proc(takes_open_file, touch_file):
                # this code block shouldn't get executed
                assert False  # noqa: B011


@pytest.mark.asyncio
async def test_open_proc_KeyboardInterrupt_while_running():
    async def do_sleep_forever():
        while True:
            await asyncio.sleep(0)

    with pytest.raises(KeyboardInterrupt):
        async with open_in_process(do_sleep_forever) as proc:
            raise KeyboardInterrupt
    assert proc.returncode == 2


# XXX: For some reason this test hangs forever if we use the open_in_proc_and_sleep_fn fixture, so
# we have to have duplicate versions of it for trio/asyncio.
@pytest.mark.asyncio
async def test_open_proc_with_trio_KeyboardInterrupt_while_running():
    sleep_fn = trio.sleep

    async def do_sleep_forever():
        while True:
            await sleep_fn(0)

    with pytest.raises(KeyboardInterrupt):
        async with open_in_process_with_trio(do_sleep_forever) as proc:
            raise KeyboardInterrupt
    assert proc.returncode == 2


class CustomException(BaseException):
    pass


@pytest.mark.asyncio
async def test_open_proc_does_not_hang_on_exception(open_in_proc_and_sleep_fn):
    open_in_proc, sleep_fn = open_in_proc_and_sleep_fn

    async def do_sleep_forever():
        while True:
            await sleep_fn(0)

    async def _do_inner():
        with pytest.raises(CustomException):
            async with open_in_proc(do_sleep_forever):
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
