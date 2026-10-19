"""Background execution helpers so the UI never blocks on the network."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str, str)  # message, detail
    progress = Signal(int, str)
    finished = Signal()  # emitted after result/error; keeps the worker alive


#: Workers are kept alive until their queued callbacks have been delivered.
_ACTIVE_WORKERS: set["Worker"] = set()


class Worker(QRunnable):
    """Runs a callable on the thread pool and reports back via signals."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        # Python owns the lifetime: Qt must not delete the runnable while queued
        # callbacks (or the signals object) are still referenced.
        self.setAutoDelete(False)
        _ACTIVE_WORKERS.add(self)
        # Deliver this last so the worker outlives every user callback.
        self.signals.finished.connect(
            lambda: _ACTIVE_WORKERS.discard(self), Qt.ConnectionType.QueuedConnection
        )

    @Slot()
    def run(self) -> None:  # pragma: no cover - exercised through the GUI
        try:
            value = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # surfaced to the UI in place of a stack trace
            detail = f"{type(exc).__name__}: {exc}"
            self.signals.error.emit(str(exc) or detail, detail)
            self.signals.finished.emit()
            return
        self.signals.result.emit(value)
        self.signals.finished.emit()


def connect_worker(
    worker: Worker,
    *,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str, str], None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> None:
    """Wire worker callbacks so they always run on the GUI thread.

    Plain Python callables have no thread affinity, so PySide6 would otherwise
    use a direct connection and invoke the callback inside the pool thread.
    Touching widgets (or a paramiko session) from there crashes the process, so
    every callback is queued explicitly.
    """
    queued = Qt.ConnectionType.QueuedConnection
    if on_result is not None:
        worker.signals.result.connect(on_result, queued)
    if on_error is not None:
        worker.signals.error.connect(on_error, queued)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress, queued)


def run_async(
    fn: Callable[..., Any],
    *args: Any,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str, str], None] | None = None,
    pool: QThreadPool | None = None,
    **kwargs: Any,
) -> Worker:
    """Schedule ``fn`` on the global thread pool."""
    worker = Worker(fn, *args, **kwargs)
    connect_worker(worker, on_result=on_result, on_error=on_error)
    (pool or QThreadPool.globalInstance()).start(worker)
    return worker
