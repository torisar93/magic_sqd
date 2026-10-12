"""Токен официальной сборки для доступа к каталогу (/content).

Сервер отдаёт каталог только тем, кто докажет, что это официальная сборка Magic SQD: в сборку
CI вшивает секрет MAGICSQD_BUILD_SECRET (в исходниках его нет, см. content_config/BuildConfig).
Клиент НЕ шлёт секрет по сети: шлёт доказательство HMAC(secret, client_id|ts) на /auth/app-token
и получает недолгий подписанный токен, который кладёт в заголовок X-App-Token на каждый запрос
к /content. Сборка из исходников секрета не знает → токен не получит.

Этот модуль — клиентская часть (доказательство + кэш токена). Серверная (проверка доказательства,
выдача токена) — server/backend.py: формат строк здесь и там должен совпадать (tests).

С 1.1.4 в запрос токена входят признаки системы (языки, раскладки, часовой пояс — app/client_env.py на ПК, Kotlin на
Android): доказательство v2 подписывает и их, сервер вшивает их в токен для правила доступа по региону
(server/access_policy.py). Подменить их, не зная секрета сборки, нельзя, VPN их не меняет. Нет признаков — v1, как
раньше.

Файл общий для ПК (app/app_token.py) и Android (android/app/src/main/python/app_token.py) —
побайтно (tests/test_app_token.py сверяет копии).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.request

# Как делается доказательство и токен — чтобы сервер и клиент считали одно и то же.
PROOF_WINDOW = 48 * 3600  # ±48 ч: на столько сервер допускает расхождение часов клиента (неверный часовой пояс
                          # на ПК техника — это целые часы; срок токена сервер отдаёт по часам клиента)
_PROOF_PREFIX = "msqd-app-token-v1"
_PROOF_PREFIX_V2 = "msqd-app-token-v2"  # с признаками системы (server/backend.py: _APP_PROOF_PREFIX_V2)


def canonical_env(env) -> str:
    """Признаки системы одной строкой — так же считает сервер (access_policy.canonical_env)."""
    return json.dumps(env, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def build_proof(build_secret: bytes, client_id: str, ts: int, env=None) -> str:
    """Доказательство владения секретом сборки: HMAC-SHA256(secret, "v1|client_id|ts"), с признаками системы —
    "v2|client_id|ts|признаки"."""
    if env is None:
        msg = f"{_PROOF_PREFIX}|{client_id}|{ts}".encode("utf-8")
    else:
        msg = f"{_PROOF_PREFIX_V2}|{client_id}|{ts}|{canonical_env(env)}".encode("utf-8")
    return hmac.new(build_secret, msg, hashlib.sha256).hexdigest()


def proof_fresh(ts: int, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    return abs(now - ts) <= PROOF_WINDOW


def request_body(build_secret: bytes, client_id: str, now: int | None = None, env=None) -> bytes:
    ts = int(time.time()) if now is None else now
    body = {"client_id": client_id, "ts": ts, "proof": build_proof(build_secret, client_id, ts, env)}
    if env is not None:
        body["env"] = env
    return json.dumps(body).encode("utf-8")


class AppToken:
    """Хранит текущий токен и обновляет его, когда до истечения осталось мало. Потокобезопасно —
    к /content ходят много потоков сразу (см. content_sync пул на 16)."""

    def __init__(self, base_url: str, build_secret: bytes, client_id: str,
                 timeout: float = 20.0, refresh_margin: float = 120.0, env_provider=None):
        # base_url — тот же origin, что /content (…/content → …/auth/app-token).
        self._token_url = base_url.rstrip("/").rsplit("/content", 1)[0] + "/auth/app-token"
        self._build_secret = build_secret
        self._client_id = client_id
        self._timeout = timeout
        self._margin = refresh_margin
        self._lock = threading.Lock()
        self._token = ""
        self._exp = 0.0
        self._opener = urllib.request.urlopen  # подменяется в тестах
        # Признаки системы для токена (с 1.1.4): функция без аргументов → dict; спрашиваем при каждом новом токене
        # (часовой пояс могли сменить). Сбой — без признаков, как раньше.
        self._env_provider = env_provider

    def _valid(self) -> bool:
        return bool(self._token) and time.time() < self._exp - self._margin

    def header(self) -> dict:
        """{"X-App-Token": …} для запроса к /content, или {} если секрета нет/токен не получить
        (на старом сервере, который токен ещё не требует, запрос всё равно пройдёт)."""
        token = self.get()
        return {"X-App-Token": token} if token else {}

    def get(self) -> str:
        if not self._build_secret:
            return ""
        with self._lock:
            if self._valid():
                return self._token
            try:
                self._fetch_locked()
            except Exception:  # noqa: BLE001 — нет сети/старый сервер: вернём пусто, запрос пойдёт без токена
                return self._token if self._valid() else ""
            return self._token

    def _env(self):
        if self._env_provider is None:
            return None
        try:
            env = self._env_provider()
        except Exception:  # noqa: BLE001 — признаки системы не обязательны
            return None
        return env if isinstance(env, dict) else None

    def _fetch_locked(self) -> None:
        body = request_body(self._build_secret, self._client_id, env=self._env())
        req = urllib.request.Request(self._token_url, data=body,
                                     headers={"Content-Type": "application/json"})
        with self._opener(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        token = data.get("token")
        exp = data.get("exp")
        if not isinstance(token, str) or not token or not isinstance(exp, (int, float)):
            raise ValueError("сервер вернул некорректный токен")
        self._token = token
        self._exp = float(exp)
