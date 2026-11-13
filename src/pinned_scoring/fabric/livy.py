"""Minimal Fabric Livy (pyspark) client using Entra tokens (no keys). Used by the graph loader script."""

import json
import time
import urllib.request

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"


class LivyError(RuntimeError):
    pass


def _call(method, url, token, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


class LivySession:
    """Context manager: opens a pyspark session on a lakehouse and always deletes it on exit."""

    def __init__(self, workspace_id, lakehouse_id, credential=None, sleep=time.sleep, timeout_s=1500):
        import os
        self._env_token = os.environ.get("FABRIC_ACCESS_TOKEN", "").strip() or None
        self._shared_session_url = os.environ.get("FABRIC_LIVY_SESSION_URL", "").strip() or None
        self._cached_token = None
        self._cached_token_exp = 0.0
        if credential is None and not self._env_token:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
        self._cred, self._sleep, self._timeout = credential, sleep, timeout_s
        self.base = (f"{API}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}"
                     "/livyapi/versions/2023-12-01/sessions")
        self.path = None
        self._owns_session = True

    def _tok(self):
        if self._env_token:
            return self._env_token
        now = time.time()
        if self._cached_token and now < self._cached_token_exp - 60:
            return self._cached_token
        tok = self._cred.get_token(SCOPE)
        self._cached_token = tok.token
        self._cached_token_exp = float(getattr(tok, "expires_on", now + 600))
        return self._cached_token

    def __enter__(self):
        if self._shared_session_url:
            self.path = self._shared_session_url
            self._owns_session = False
            return self
        session_req = {
            "kind": "pyspark",
            "driverCores": 4,
            "driverMemory": "28g",
            "executorCores": 4,
            "executorMemory": "28g",
            "numExecutors": 1,
        }
        sid = _call("POST", self.base, self._tok(), session_req)["id"]
        self.path = f"{self.base}/{sid}"
        waited = 0
        while True:
            state = _call("GET", self.path, self._tok())["state"]
            if state == "idle":
                return self
            if state in ("dead", "error", "killed"):
                raise LivyError(f"session failed: {state}")
            if waited > self._timeout:
                raise LivyError("session start timed out")
            self._sleep(10)
            waited += 10

    def run(self, code):
        st = _call("POST", f"{self.path}/statements", self._tok(), {"code": code, "kind": "pyspark"})
        while st.get("state") not in ("available", "error", "cancelled"):
            self._sleep(3)
            st = _call("GET", f"{self.path}/statements/{st['id']}", self._tok())
        out = st.get("output") or {}
        if out.get("status") != "ok":
            raise LivyError(f"{out.get('ename')}: {out.get('evalue')}")
        return (out.get("data") or {}).get("text/plain", "")

    def __exit__(self, *exc):
        if self._owns_session and self.path:
            try:
                _call("DELETE", self.path, self._tok())
            except Exception:  # best-effort cleanup
                pass
        return False
