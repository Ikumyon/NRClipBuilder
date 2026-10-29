"""Qt workers with cancellation and per-channel stale-result rejection."""
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from . import _native


class Worker(QRunnable):
    def __init__(self, signals, key, generation, token, operation):
        super().__init__()
        self.signals, self.key, self.generation, self.token, self.operation = signals, key, generation, token, operation

    def run(self):
        try:
            self.token.check()
            result = self.operation(self.token)
            self.signals.completed.emit(self.key, self.generation, result, None)
        except Exception as exc:
            self.signals.completed.emit(self.key, self.generation, None, exc)


class JobSignals(QObject):
    completed = Signal(str, int, object, object)


class Jobs(QObject):
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.signals = JobSignals(self)
        self.signals.completed.connect(self._complete)
        self.generations, self.active = {}, {}
        self.inflight = set()

    def submit(self, key, operation, done, failed=None, discard=None):
        if key in self.active:
            self.active[key][1].cancel()
        generation = self.generations.get(key, 0) + 1
        self.generations[key] = generation
        token = _native.CancelToken()
        self.active[key] = generation, token, done, failed, discard
        self.inflight.add((key, generation))
        self.busy_changed.emit(True)
        self.pool.start(Worker(self.signals, key, generation, token, operation))
        return token

    @Slot(str, int, object, object)
    def _complete(self, key, generation, result, error):
        self.inflight.discard((key, generation))
        self.busy_changed.emit(bool(self.inflight))
        current = self.active.get(key)
        if current is None or current[0] != generation:
            return
        _, _, done, failed, discard = self.active.pop(key)
        if error is None:
            done(result)
        elif "cancelled" in str(error):
            if discard:
                discard()
        elif failed:
            failed(error)
        else:
            self.failed.emit(str(error))

    def cancel(self):
        for key, (_, token, _, _, _) in self.active.items():
            if key not in {"save", "startup", "history"}:
                token.cancel()

    def running(self, key):
        return key in self.active
