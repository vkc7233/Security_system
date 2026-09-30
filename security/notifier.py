"""Non-blocking Telegram alert delivery (queue + worker thread)."""
import logging
import queue
import threading

import requests

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self._q = queue.Queue(maxsize=100)
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True, name="telegram").start()

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def send(self, text, photo_path=None):
        """Queue an alert; never blocks the camera loop."""
        if not self.enabled:
            return False
        try:
            self._q.put_nowait((text, photo_path))
            return True
        except queue.Full:
            log.warning("Telegram queue full, dropping alert")
            return False

    def _worker(self):
        base = f"https://api.telegram.org/bot{self.token}"
        while True:
            text, photo_path = self._q.get()
            try:
                if photo_path:
                    with open(photo_path, "rb") as fh:
                        r = requests.post(f"{base}/sendPhoto", timeout=15,
                                          data={"chat_id": self.chat_id, "caption": text[:1000]},
                                          files={"photo": fh})
                else:
                    r = requests.post(f"{base}/sendMessage", timeout=15,
                                      data={"chat_id": self.chat_id, "text": text})
                if r.status_code != 200:
                    log.warning("Telegram send failed: %s", r.text[:200])
            except Exception as exc:  # network errors must not kill the worker
                log.warning("Telegram error: %s", exc)
