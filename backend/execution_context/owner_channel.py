"""Read-only Windows owner attestation; renew endpoints before dispatch intent only."""
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from django.conf import settings
from .owner_tools import OwnerTools, OwnerToolsError, load_config, save_config

PIPE_PATTERN = re.compile(r'\\\\\.\\pipe\\codex-app-tools-[0-9a-f-]{36}\Z')


def pipe_identity(pipe):
    """Ask Windows for the pipe server image and token SID, never read its protocol."""
    if os.name != 'nt' or not PIPE_PATTERN.fullmatch(pipe):
        raise OwnerToolsError('صيغة قناة المالك غير معتمدة')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.OpenProcess.argtypes = [w.DWORD,w.BOOL,w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetCurrentProcess.restype = w.HANDLE
    handles = []
    try:
        handle = kernel.CreateFileW(pipe, 0, 3, None, 3, 0, None)
        if handle == w.HANDLE(-1).value:
            raise OSError()
        handles.append(handle)
        pid = w.ULONG()
        if not kernel.GetNamedPipeServerProcessId(w.HANDLE(handle), ctypes.byref(pid)):
            raise OSError()
        process = kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            raise OSError()
        handles.append(process)
        image = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(w.HANDLE(process), 0, image, ctypes.byref(size)):
            raise OSError()

        def sid(process_handle):
            token = w.HANDLE()
            if not security.OpenProcessToken(w.HANDLE(process_handle), 8, ctypes.byref(token)):
                raise OSError()
            handles.append(token.value)
            length = w.DWORD()
            security.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            data = ctypes.create_string_buffer(length.value)
            if not security.GetTokenInformation(token, 1, data, length, ctypes.byref(length)):
                raise OSError()
            sid_pointer = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            result = w.LPWSTR()
            if not security.ConvertSidToStringSidW(ctypes.c_void_p(sid_pointer), ctypes.byref(result)):
                raise OSError()
            try:
                return result.value
            finally:
                kernel.LocalFree(ctypes.cast(result, ctypes.c_void_p))

        owner_sid = sid(process)
        if owner_sid != sid(kernel.GetCurrentProcess()):
            raise OSError()
        path = str(Path(image.value).resolve())
        return {'image': path, 'sid': owner_sid,
                'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    except (OSError, ValueError) as exc:
        raise OwnerToolsError('تعذر إثبات مالك القناة المحلي؛ لا إرسال') from exc
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(w.HANDLE(handle))


def bootstrap_trust(pipe):
    identity = pipe_identity(pipe)
    # Only initial setup inspects Authenticode. Renewal pins the exact signed image.
    script = "$s=Get-AuthenticodeSignature -LiteralPath $env:OWNER_IMAGE; @{'status'=[string]$s.Status;'subject'=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env={**{k:v for k,v in os.environ.items() if k.lower() != 'psmodulepath'},
             'OWNER_IMAGE': identity['image']}, capture_output=True, text=True, timeout=30)
    try:
        signature = json.loads(result.stdout)
        if result.returncode or signature['status'] != 'Valid' or 'O="OpenAI OpCo, LLC"' not in signature['subject']:
            raise ValueError()
        path = Path(identity['image'])
        if path.name.lower() != 'chatgpt.exe' or not any(p.startswith('OpenAI.Codex_') for p in path.parts):
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise OwnerToolsError('توقيع تطبيق Codex غير معتمد') from exc
    return identity


def candidate_pipes():
    try:
        return sorted('\\\\.\\pipe\\' + name for name in os.listdir('\\\\.\\pipe\\')
                      if PIPE_PATTERN.fullmatch('\\\\.\\pipe\\' + name))
    except OSError as exc:
        raise OwnerToolsError('تعذر اكتشاف قناة المالك المحلية') from exc


def _probe(config):
    with OwnerTools(config) as client:
        data = client.call('read_thread', {'threadId':config['caller_thread_id'], 'turnLimit':1,
            'includeOutputs':False,'maxOutputCharsPerItem':50},
            call_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()))
    thread = data.get('thread', {})
    if (thread.get('id') != config['caller_thread_id'] or thread.get('kind') != 'codex'
            or thread.get('hostId') != 'local'
            or Path(thread.get('cwd','')).resolve() != settings.BASE_DIR.parent.resolve()):
        raise OwnerToolsError('هوية جلسة قناة المالك غير مطابقة')


def ensure_channel(config):
    """Renew only the pipe, with a unique attested owner and official read proof.

    Never called from send/reconcile/retry paths. No model request is made here.
    An app binary upgrade requires explicit reconfiguration of the pinned trust.
    """
    trust = config.get('owner_identity')
    if not trust:
        raise OwnerToolsError('يلزم اعتماد هوية تطبيق المالك مرة واحدة')
    try:
        if pipe_identity(config['pipe']) == trust:
            return config
    except OwnerToolsError:
        pass
    matching = []
    for pipe in candidate_pipes():
        try:
            if pipe_identity(pipe) == trust:
                matching.append(pipe)
        except OwnerToolsError:
            continue
    if len(matching) != 1:
        raise OwnerToolsError('قناة المالك غائبة أو متعددة؛ لا تجديد ولا إرسال')
    fresh = {**config, 'pipe':matching[0]}
    _probe(fresh)
    if pipe_identity(fresh['pipe']) != trust or load_config() != config:
        raise OwnerToolsError('تغيرت هوية أو إعداد قناة المالك أثناء التحقق')
    save_config(fresh, expected=config)
    return fresh
