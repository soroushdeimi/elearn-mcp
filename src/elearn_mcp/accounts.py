"""Multi-account session manager: one SSO login serves every student id."""

import os
import subprocess
import threading

from elearn_mcp import moodle, sso


def load_credentials() -> tuple[str, str]:
    username = os.environ.get("ELEARN_USERNAME")
    if not username:
        raise RuntimeError(
            "ELEARN_USERNAME is not set; export it or store the password in the "
            "macOS Keychain under service 'elearn-mcp'"
        )
    password = os.environ.get("ELEARN_PASSWORD")
    if not password:
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "elearn-mcp", "-a", username, "-w"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:  # no Keychain outside macOS
            result = None
        if result is None or result.returncode != 0:
            raise RuntimeError(
                "ELEARN_PASSWORD is not set and no Keychain entry was found; "
                "export ELEARN_PASSWORD or run: "
                f"security add-generic-password -s elearn-mcp -a {username} -w"
            )
        password = result.stdout.strip()
    return username, password


class Accounts:
    def __init__(self, username: str, password: str, login=sso.login, list_accounts=sso.accounts):
        self._username = username
        self._password = password
        self._login = login
        self._list_accounts = list_accounts
        self._sso = sso.new_client()
        self._ids: list[str] | None = None
        self._sessions: dict[str, sso.Session] = {}
        self._lock = threading.Lock()

    def ids(self) -> list[str]:
        if self._ids is None:
            self._ids = self._list_accounts(self._username, self._password, client=self._sso)
        return self._ids

    def session(self, student_id: str) -> sso.Session:
        if student_id not in self.ids():
            raise ValueError(f"unknown student id: {student_id}")
        if student_id not in self._sessions:
            with self._lock:
                if student_id not in self._sessions:
                    self._sessions[student_id] = self._login(
                        self._username,
                        self._password,
                        student_id,
                        client=sso.clone_sso_cookies(self._sso),
                    )
        return self._sessions[student_id]

    def run(self, student_id: str, fn):
        try:
            return fn(self.session(student_id))
        except moodle.SessionExpired:
            with self._lock:
                self._sessions.pop(student_id, None)
            return fn(self.session(student_id))
