"""Worker threading: callbacks must always run on the GUI thread.

Regression guard for the crash where a plain-callable connection was invoked
directly inside the pool thread and updated widgets from there.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from nexus_remote.ui.tasks import Worker, connect_worker, run_async


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def _pump(app, predicate, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.005)
    assert predicate(), "callback never arrived"


def test_result_callback_runs_on_the_gui_thread(app):
    main_thread = threading.main_thread()
    seen: list[tuple[threading.Thread, object]] = []
    worker = Worker(lambda: "value")
    connect_worker(worker, on_result=lambda value: seen.append((threading.current_thread(), value)))
    QThreadPool.globalInstance().start(worker)
    _pump(app, lambda: bool(seen))
    thread, value = seen[0]
    assert value == "value"
    assert thread is main_thread


def test_error_callback_runs_on_the_gui_thread(app):
    main_thread = threading.main_thread()
    seen: list[tuple[threading.Thread, str]] = []

    def boom():
        raise RuntimeError("expected failure")

    worker = Worker(boom)
    connect_worker(worker, on_error=lambda message, detail: seen.append((threading.current_thread(), detail)))
    QThreadPool.globalInstance().start(worker)
    _pump(app, lambda: bool(seen))
    thread, detail = seen[0]
    assert "expected failure" in detail
    assert thread is main_thread


def test_progress_callback_runs_on_the_gui_thread(app):
    main_thread = threading.main_thread()
    seen: list[threading.Thread] = []

    def work(progress_cb=None):
        progress_cb(1, 2)
        return "done"

    worker = Worker(work)
    worker.kwargs["progress_cb"] = worker.signals.progress.emit
    connect_worker(worker, on_result=lambda _value: None, on_progress=lambda *_args: seen.append(threading.current_thread()))
    QThreadPool.globalInstance().start(worker)
    _pump(app, lambda: bool(seen))
    assert seen[0] is main_thread


def test_run_async_helper_queues_callbacks(app):
    main_thread = threading.main_thread()
    seen: list[threading.Thread] = []
    run_async(lambda: 7, on_result=lambda _value: seen.append(threading.current_thread()))
    _pump(app, lambda: bool(seen))
    assert seen[0] is main_thread


def test_worker_is_released_after_callbacks(app):
    from nexus_remote.ui.tasks import _ACTIVE_WORKERS

    before = len(_ACTIVE_WORKERS)
    seen: list[object] = []
    worker = Worker(lambda: "byebye")
    connect_worker(worker, on_result=seen.append)
    QThreadPool.globalInstance().start(worker)
    _pump(app, lambda: bool(seen))
    _pump(app, lambda: len(_ACTIVE_WORKERS) <= before)
    assert len(_ACTIVE_WORKERS) <= before
