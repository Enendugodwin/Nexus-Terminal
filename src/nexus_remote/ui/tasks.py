"""Background execution helpers so the UI never blocks on the network."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str, str)  # message, detail
    progress = Signal(int, str)


class Worker(QRunnable):
    """Runs a callable on the thread pool and reports back via signals."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:  # pragma: no cover - exercised through the GUI
        try:
            value = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # surfaced to the UI in place of a stack trace
            detail = f"{type(exc).__name__}: {exc}"
            self.signals.error.emit(str(exc) or detail, detail)
            return
        self.signals.result.emit(value)


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
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    (pool or QThreadPool.globalInstance()).start(worker)
    return worker
