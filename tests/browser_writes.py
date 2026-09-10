"""Isolated Chrome smoke test for Phase 3.1/3.2 (no live Supabase writes).

Run from backend: .venv/bin/python -m tests.browser_writes
Requires Google Chrome/Chromium, npm dependencies and backend dev dependencies.
Starts the actual Vite frontend and FastAPI app with a temporary SQLite database
and locally signed JWTs, exercising the production JWT verifier and DB dependency.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import jwt
import websockets

ROOT = Path(__file__).resolve().parents[2]
USER_ID = "aaaaaaaa-1111-4111-8111-111111111111"
OTHER_ID = "bbbbbbbb-2222-4222-8222-222222222222"
SECRET = "isolated-browser-test-secret-not-for-production-123456789"


def token(user_id=USER_ID):
    return jwt.encode({"sub": user_id, "email": "browser@example.test", "aud": "authenticated", "role": "authenticated",
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, SECRET, algorithm="HS256")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def seed():
    from app.db.session import engine, async_session_factory
    from app.models.base import Base
    from app.models.profile import Profile
    from app.models.friend import Friend
    import app.models  # Register all tables in the isolated database.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as db:
        db.add_all([Profile(user_id=uuid.UUID(USER_ID), email="browser@example.test", display_name="Browser User"),
                    Profile(user_id=uuid.UUID(OTHER_ID), email="other@example.test", display_name="Other User"),
                    Friend(user_id=uuid.UUID(USER_ID), friend_id=uuid.UUID(OTHER_ID), status="accepted")])
        await db.commit()
    await engine.dispose()


class Browser:
    def __init__(self, ws):
        self.ws, self.sequence = ws, 0

    async def call(self, method, **params):
        self.sequence += 1
        await self.ws.send(json.dumps({"id": self.sequence, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise AssertionError(message["error"])
                return message.get("result", {})

    async def evaluate(self, expression):
        result = await self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise AssertionError(result["exceptionDetails"].get("text", "Browser script failed"))
        return result.get("result", {}).get("value")

    async def wait(self, expression):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if await self.evaluate(expression):
                return
            await asyncio.sleep(.1)
        raise AssertionError(f"Browser condition timed out: {expression}\n" + str(await self.evaluate("document.body.innerText"))[:2500])

    async def fill(self, selector, value):
        await self.wait(f"!!document.querySelector({json.dumps(selector)})")
        await self.evaluate(f"""(() => {{const input = document.querySelector({json.dumps(selector)});
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, {json.dumps(value)});
            input.dispatchEvent(new Event('input', {{bubbles:true}}));}})()""")

    async def click(self, selector):
        await self.wait(f"!!document.querySelector({json.dumps(selector)})")
        await self.evaluate(f"document.querySelector({json.dumps(selector)}).click()")

    async def click_text(self, label):
        expression = f"[...document.querySelectorAll('button')].find(b => b.textContent.trim() === {json.dumps(label)} && !b.disabled)"
        await self.wait(f"!!({expression})")
        await self.evaluate(f"({expression}).click()")

    async def api(self, method, path, body=None):
        args = json.dumps(path) + (", " + json.dumps(body) if body is not None else "")
        response = await self.evaluate(f"window.apiClient.{method}({args})")
        assert response is not None and not response["error"], response
        return response


async def wait_http(url):
    async with httpx.AsyncClient(trust_env=False) as client:
        for _ in range(200):
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return response
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.1)
    raise AssertionError(f"Server did not start: {url}")


async def exercise(frontend, api, debugger):
    pages = (await wait_http(f"{debugger}/json/list")).json()
    target = next(page for page in pages if page["type"] == "page")
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=4_000_000) as ws:
        browser = Browser(ws)
        await browser.call("Page.enable")
        await browser.call("Runtime.enable")
        session = {"access_token": token(), "refresh_token": "isolated-unused-refresh", "token_type": "bearer", "expires_in": 3600,
                   "expires_at": int(time.time()) + 3600,
                   "user": {"id": USER_ID, "aud": "authenticated", "role": "authenticated", "email": "browser@example.test",
                            "email_confirmed_at": "2026-01-01T00:00:00Z", "confirmed_at": "2026-01-01T00:00:00Z",
                            "app_metadata": {}, "user_metadata": {}, "created_at": "2026-01-01T00:00:00Z"}}
        await browser.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('sb-127-auth-token', {json.dumps(json.dumps(session))});")
        await browser.call("Page.navigate", url=f"{frontend}/profile")
        await browser.wait("document.querySelector('#displayName')?.value === 'Browser User'")
        await browser.fill("#displayName", "Browser Renamed")
        await browser.click_text("Save Changes")
        await browser.wait("document.body.innerText.includes('Profile updated!')")
        assert (await browser.api("get", "/api/v1/users/me"))["data"]["display_name"] == "Browser Renamed"
        print("PASS browser: profile update", flush=True)

        await browser.fill('input[placeholder="New category name..."]', "Browser Hobby")
        await browser.click_text("Add")
        await browser.click('[aria-label="Rename Browser Hobby"]')
        await browser.fill('[aria-label="Category name"]', "Browser Leisure")
        await browser.click('[aria-label="Save category name"]')
        await browser.wait('!!document.querySelector(\'[aria-label="Rename Browser Leisure"]\')')
        cat = (await browser.api("get", "/api/v1/categories/"))["data"][0]
        assert cat["name"] == "Browser Leisure" and cat["slug"] == "browser_hobby"
        print("PASS browser: category create and rename", flush=True)

        await browser.call("Page.navigate", url=f"{frontend}/expenses")
        await browser.click_text("Add Expense")
        await browser.fill('[role="dialog"] input[type="number"]', "24.50")
        await browser.fill('input[placeholder="Dinner, cab..."]', "Browser Lunch")
        await browser.click_text("Save Expense")
        await browser.click('[aria-label="Edit Browser Lunch"]')
        await browser.fill('[role="dialog"] input[type="number"]', "30.25")
        await browser.fill('input[placeholder="Dinner, cab..."]', "Browser Lunch Edited")
        await browser.click_text("Update Expense")
        await browser.wait('!!document.querySelector(\'[aria-label="Edit Browser Lunch Edited"]\')')
        rows = (await browser.api("get", "/api/v1/expenses/personal"))["data"]
        assert next(row for row in rows if row["note"] == "Browser Lunch Edited")["amount"] == 30.25
        await browser.click('[aria-label="Delete Browser Lunch Edited"]')
        await browser.click_text("Delete")
        await browser.wait('!document.querySelector(\'[aria-label="Edit Browser Lunch Edited"]\')')
        assert (await browser.api("get", "/api/v1/expenses/personal"))["data"] == []
        print("PASS browser: personal expense UI create/edit/delete", flush=True)

        # Audited personal UI has no split editor. Exercise optional split payloads
        # through the actual browser apiClient; friend-form writes are a later phase.
        splits = [{"user_id": USER_ID, "amount": 50}, {"user_id": OTHER_ID, "amount": 50}]
        created = (await browser.api("post", "/api/v1/expenses/personal", {"amount": 100, "category": cat["slug"], "splits": splits}))["data"]
        assert (await browser.api("get", "/api/v1/friends/balances"))["data"][0]["netBalance"] == 50
        await browser.api("patch", f"/api/v1/expenses/{created['id']}", {"amount": 120, "splits": [{**s, "amount": 60} for s in splits]})
        assert (await browser.api("get", "/api/v1/friends/balances"))["data"][0]["netBalance"] == 60

        await browser.call("Page.navigate", url=f"{frontend}/profile")
        await browser.click('[aria-label="Delete Browser Leisure"]')
        await browser.wait('!document.querySelector(\'[aria-label="Rename Browser Leisure"]\')')
        feed = (await browser.api("get", f"/api/v1/friends/{OTHER_ID}/expenses"))["data"]
        assert feed[0]["category"] == "browser_hobby"
        await browser.api("delete", f"/api/v1/expenses/{created['id']}")
        assert (await browser.api("get", "/api/v1/friends/balances"))["data"] == []
        print("PASS browser: referenced category hard delete; split expense create/edit/delete and balances via apiClient", flush=True)

        # Real signed JWT for another test user; requests are issued by the browser.
        async def other_request(method, path, body=None):
            options = {"method": method, "headers": {"Content-Type": "application/json", "Authorization": f"Bearer {token(OTHER_ID)}"}}
            if body is not None:
                options["body"] = json.dumps(body)
            return await browser.evaluate(f"fetch({json.dumps(api + path)}, {json.dumps(options)}).then(async r => ({{status:r.status, body:await r.json()}}))")
        other_cat = await other_request("POST", "/api/v1/categories/", {"name": "Other Category"})
        other_expense = await other_request("POST", "/api/v1/expenses/personal", {"amount": 10, "category": "food"})
        assert other_cat["status"] == other_expense["status"] == 201
        for path, payload in [(f"/api/v1/categories/{other_cat['body']['data']['id']}", {"name": "Stolen"}),
                              (f"/api/v1/expenses/{other_expense['body']['data']['id']}", {"amount": 1})]:
            for method in ("PATCH", "DELETE"):
                options = {"method": method, "headers": {"Content-Type": "application/json", "Authorization": f"Bearer {token()}"}}
                if method == "PATCH":
                    options["body"] = json.dumps(payload)
                response = await browser.evaluate(f"fetch({json.dumps(api + path)}, {json.dumps(options)}).then(r => r.status)")
                assert response == 404, response
        rejection = await browser.evaluate("window.apiClient.patch('/api/v1/users/me', {email:'stolen@example.test'})")
        assert rejection["error"] and rejection["data"] is None
        print("PASS browser: other-user category/expense writes and protected profile fields rejected", flush=True)


async def main():
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        raise SystemExit("Google Chrome or Chromium is required")
    with tempfile.TemporaryDirectory(prefix="expense-browser-") as temp:
        api_port, frontend_port, chrome_port = port(), port(), port()
        api, frontend = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{frontend_port}"
        env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{temp}/test.sqlite", "SUPABASE_URL": api,
               "SUPABASE_JWT_SECRET": SECRET, "CORS_ORIGINS": json.dumps([frontend]),
               "VITE_API_URL": api, "VITE_SUPABASE_URL": api, "VITE_SUPABASE_PUBLISHABLE_KEY": "isolated-test-public-key"}
        processes = []
        try:
            with open(f"{temp}/servers.log", "w+") as log:
                processes.append(subprocess.Popen([sys.executable, "-m", "tests.browser_writes", "--serve", str(api_port)], cwd=ROOT / "backend", env=env, stdout=log, stderr=log))
                processes.append(subprocess.Popen(["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"], cwd=ROOT / "frontend", env=env, stdout=log, stderr=log, start_new_session=True))
                processes.append(subprocess.Popen([chrome, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
                                                  f"--user-data-dir={temp}/chrome", f"--remote-debugging-port={chrome_port}", "about:blank"], stdout=log, stderr=log))
                await wait_http(f"{api}/api/v1/health")
                await wait_http(frontend)
                await exercise(frontend, api, f"http://127.0.0.1:{chrome_port}")
        finally:
            import signal
            for index, process in enumerate(processes):
                if process.poll() is None:
                    if index == 1:
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        import uvicorn
        asyncio.run(seed())
        uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[-1]), log_level="warning")
    else:
        asyncio.run(main())
