"""Deliver one approved outbox task to its existing Codex App Server thread."""

import io
import json
import os
import queue
import shutil
import subprocess
import threading
import time

from django.core.management import call_command
from django.db import close_old_connections
from execution_context.models import SessionDispatch
from execution_context.rollout_usage import find_rollout


class BridgeError(RuntimeError):
    pass


class AppServer:
    def __init__(self, binary, timeout=30):
        self.process = subprocess.Popen(
            [binary, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            shell=False,
        )
        self.inbox = queue.Queue()
        self.events = []
        self.sequence = 0
        self.timeout = timeout
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()
        self.rpc("initialize", {"clientInfo": {
            "name": "shadow-danger-session-bridge",
            "title": "ظل الخطر - جسر الجلسات",
            "version": "1.0.0",
        }})
        self.send({"method": "initialized"})

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                try:
                    self.inbox.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.inbox.put(None)

    def send(self, message):
        if self.process.poll() is not None:
            raise BridgeError("انتهى App Server قبل اكتمال التسليم")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _receive(self, timeout):
        try:
            message = self.inbox.get(timeout=timeout)
        except queue.Empty as exc:
            raise BridgeError("انتهت مهلة استجابة App Server") from exc
        if message is None:
            raise BridgeError("توقف App Server أثناء تسليم المهمة")
        # This worker has no interactive approval UI. Deny server-side prompts
        # rather than leaving an unhandled request or granting extra permissions.
        if "method" in message and "id" in message:
            self.send({"id": message["id"], "error": {
                "code": -32601,
                "message": "No interactive approval service is available in this bridge",
            }})
            raise BridgeError("طلبت الجلسة موافقة تفاعلية؛ أوقف الجسر الطلب بأمان")
        return message

    def rpc(self, method, params, timeout=None):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            message = self._receive(max(0.05, deadline - time.monotonic()))
            if message.get("id") == request_id:
                if "error" in message:
                    detail = message["error"].get("message", "App Server رفض الطلب")
                    raise BridgeError(str(detail)[:300])
                return message.get("result", {})
            self.events.append(message)
        raise BridgeError("انتهت مهلة استجابة App Server")

    @staticmethod
    def _thread(result):
        return result.get("thread", result)

    @staticmethod
    def _status(thread):
        return (thread.get("status") or {}).get("type", "unknown")

    def inspect_thread(self, thread_id):
        result = self.rpc("thread/read", {"threadId": thread_id})
        thread = self._thread(result)
        if thread.get("id") != thread_id:
            raise BridgeError("لم يطابق App Server هوية الجلسة المطلوبة")
        return self._status(thread)

    def resume_idle_thread(self, thread_id):
        result = self.rpc("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        thread = self._thread(result)
        if thread.get("id") != thread_id:
            raise BridgeError("استعاد App Server محادثة مختلفة عن الوجهة المحددة")
        status = self._status(thread)
        if status not in {"idle", "notLoaded"}:
            return status
        return status

    def start_turn(self, thread_id, prompt, model, effort):
        result = self.rpc("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "model": model,
            "effort": effort,
        })
        turn = result.get("turn") or {}
        if not turn.get("id"):
            raise BridgeError("لم يُرجع App Server معرف الدور؛ نتيجة الإرسال ملتبسة")
        return turn["id"]

    def wait_for_turn(self, thread_id, turn_id, timeout=7200):
        deadline = time.monotonic() + timeout
        messages = []
        while time.monotonic() < deadline:
            event = self.events.pop(0) if self.events else self._receive(max(0.05, deadline - time.monotonic()))
            params = event.get("params") or {}
            if params.get("threadId") != thread_id:
                continue
            if params.get("turnId") not in (None, turn_id):
                continue
            if event.get("method") == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    messages.append(item["text"])
            if event.get("method") == "turn/completed" and (params.get("turn") or {}).get("id") == turn_id:
                turn = params["turn"]
                return turn.get("status") == "completed", "\n".join(messages)
        raise BridgeError("انتهت مهلة الدور؛ حُظر إعادة الإرسال التلقائية")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        try:
            self.process.stdin.close()
            self.process.stdout.close()
        except OSError:
            pass


def _binary():
    configured = os.environ.get("CODEX_APP_RUNTIME_CLI_PATH")
    if configured and os.path.isfile(configured):
        return configured
    for candidate in (shutil.which("codex.exe"), shutil.which("codex")):
        if candidate:
            return candidate
    # Scheduled Tasks often start with a reduced PATH and do not inherit the
    # interactive Codex runtime variable. Reuse the project's installed-binary
    # resolver so the local bridge can still reach the bundled App Server.
    from workspace.dispatcher import resolve_codex
    candidate = resolve_codex()
    if candidate and os.path.isfile(candidate):
        return candidate
    raise BridgeError("لم يُعثر على Codex App Server؛ شغّل جسر الجلسات على جهاز Codex")


def _command(action, dispatch_id, claim_token, *, turn_id="", detail=None):
    output = io.StringIO()
    call_command(
        "session_outbox",
        action,
        dispatch=dispatch_id,
        claim_token=str(claim_token),
        turn_id=turn_id,
        detail=json.dumps(detail or {}, ensure_ascii=False),
        stdout=output,
    )
    return json.loads(output.getvalue())


def _release_claim(dispatch_id, claim_token, reason):
    from django.db import transaction
    from django.utils import timezone
    from execution_context.models import SessionDispatchEvent
    with transaction.atomic():
        dispatch = SessionDispatch.objects.select_for_update().select_related("job").get(
            pk=dispatch_id, claim_token=claim_token, state="claimed")
        dispatch.state = "queued"
        dispatch.claim_token = None
        dispatch.claimed_by = ""
        dispatch.claimed_at = None
        dispatch.save(update_fields=["state", "claim_token", "claimed_by", "claimed_at", "updated_at"])
        dispatch.job.state = "waiting_delivery"
        dispatch.job.started_at = None
        dispatch.job.save(update_fields=["state", "started_at"])
        SessionDispatchEvent.objects.create(dispatch=dispatch, state="deferred_busy", detail={"reason": reason})


def _mark_uncertain(dispatch_id, claim_token, reason):
    try:
        dispatch = SessionDispatch.objects.get(pk=dispatch_id, claim_token=claim_token)
        if dispatch.state in {"claimed", "sent", "acknowledged"}:
            _command("uncertain", dispatch_id, claim_token, detail={"reason": reason[:300]})
    except Exception:
        # Preserve the original failure; the persisted state remains available for
        # manual reconciliation if the database itself rejected the transition.
        pass


def _rollout_has_active_turn(thread_id):
    """Check persisted Codex turn boundaries, including turns owned by the UI process."""
    path = find_rollout(thread_id)
    started = set()
    completed = set()
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    raise BridgeError("ملف الجلسة يحتوي سجلًا غير مكتمل؛ لا مطالبة آمنة") from exc
                if record.get("type") != "event_msg":
                    continue
                payload = record.get("payload") or {}
                turn_id = payload.get("turn_id")
                if not turn_id:
                    continue
                if payload.get("type") == "task_started":
                    started.add(turn_id)
                elif payload.get("type") == "task_complete":
                    completed.add(turn_id)
    except OSError as exc:
        raise BridgeError("تعذر فحص حدود الدور في ملف الجلسة") from exc
    return bool(started - completed)


def deliver_one():
    """Deliver one queued workspace-write job; no Codex process when the queue is empty."""
    close_old_connections()
    candidate = SessionDispatch.objects.filter(
        state="queued", job__mode="workspace-write").order_by("created_at", "pk").first()
    if not candidate:
        return None
    bridge = None
    dispatch_id = candidate.pk
    turn_started = False
    turn_start_attempted = False
    try:
        bridge = AppServer(_binary())
        status = bridge.inspect_thread(candidate.target_thread_id)
        if status == "active":
            return None
        if status not in {"idle", "notLoaded"}:
            raise BridgeError(f"حالة الجلسة لا تسمح بالإرسال الآمن: {status}")
        if _rollout_has_active_turn(candidate.target_thread_id):
            return None
        output = io.StringIO()
        call_command("session_outbox", "claim", consumer="desktop-app-server", stdout=output)
        claimed = json.loads(output.getvalue() or "{}")
        if not claimed:
            return None
        dispatch_id = claimed["dispatch_id"]
        token = claimed["claim_token"]
        if claimed.get("thread_id") != candidate.target_thread_id:
            _release_claim(dispatch_id, token, "تغيرت وجهة الدور بين الفحص والمطالبة")
            return None

        status = bridge.resume_idle_thread(claimed["thread_id"])
        if status not in {"idle", "notLoaded"}:
            _release_claim(dispatch_id, token, "الجلسة أصبحت مشغولة قبل بدء الدور")
            return None
        if _rollout_has_active_turn(claimed["thread_id"]):
            _release_claim(dispatch_id, token, "وجد ملف الجلسة دورًا مفتوحًا قبل بدء المهمة")
            return None

        turn_start_attempted = True
        turn_id = bridge.start_turn(
            claimed["thread_id"], claimed["prompt"], claimed["model"], claimed["thinking"])
        turn_started = True
        _command("sent", dispatch_id, token, turn_id=turn_id,
                 detail={"transport": "codex-app-server-stdio", "thread_id": claimed["thread_id"]})
        completed, result_text = bridge.wait_for_turn(claimed["thread_id"], turn_id)
        if not completed:
            _mark_uncertain(dispatch_id, token, "أكمل App Server الدور بحالة غير مكتملة؛ لا إعادة تلقائية")
            return dispatch_id
        _command("acknowledged", dispatch_id, token, turn_id=turn_id,
                 detail={"transport": "codex-app-server-stdio", "turn_id": turn_id})
        if "ESCALATION_REQUEST:" in result_text:
            reason = result_text.split("ESCALATION_REQUEST:", 1)[1].strip().splitlines()[0][:300]
            _command("stuck", dispatch_id, token, turn_id=turn_id, detail={"reason": reason or "تعثر صريح موثق"})
        else:
            _command("complete-from-rollout", dispatch_id, token, turn_id=turn_id,
                     detail={"transport": "codex-app-server-stdio"})
        return dispatch_id
    except Exception as exc:
        if "token" in locals():
            if turn_started or (turn_start_attempted and 'already has an active writer' not in str(exc)):
                _mark_uncertain(dispatch_id, token, str(exc))
            else:
                try:
                    _release_claim(dispatch_id, token, str(exc)[:300])
                except Exception:
                    _mark_uncertain(dispatch_id, token, str(exc))
        raise BridgeError(str(exc)[:300]) from exc
    finally:
        if bridge:
            bridge.close()
        close_old_connections()
