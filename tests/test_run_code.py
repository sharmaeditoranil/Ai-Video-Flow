"""
Tests for Smart AI's own-code runner: off by default, the tool appears only when allowed, scripts run with
the app's Python and packages, time limits, the launcher's --avf-run-script mode and, on macOS, the sandbox
(write only to the work / output folders, no internet).
"""

import os
import platform
import subprocess
import sys
import uuid

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def b(tmp_path, monkeypatch):
    backend = pytest.importorskip("packages.desktop_app.backend")
    monkeypatch.setattr(backend, "OUTPUT_DIR", str(tmp_path / "out"))
    os.makedirs(tmp_path / "out", exist_ok=True)
    return backend


def test_off_by_default_and_tool_hidden(b):
    assert b.smart_ai_settings()["allow_code"] is False
    assert "run_code" not in [t["name"] for t in b._assistant_tools()]
    with pytest.raises(RuntimeError):
        b._assistant_execute("")("run_code", {"code": "print(1)", "purpose": "x"})


def test_switch_on_adds_the_tool(b):
    assert b.save_smart_ai_settings(True)["allow_code"] is True
    names = [t["name"] for t in b._assistant_tools()]
    assert names.count("run_code") == 1
    out = b._assistant_execute("")("run_code", {"code": "print('hi')", "purpose": "test", "timeout": 30})
    assert out["exit_code"] == 0 and "hi" in out["stdout"]


def test_script_uses_app_python_and_env(b):
    code = ("import os, numpy, cv2\n"
            "from packages.ai_engine.story_planner import retime_for\n"
            "open(os.path.join(os.environ['AVF_WORK'], 'result.txt'), 'w').write('ok')\n"
            "print(retime_for(True, False, 50.0)[0], bool(os.environ.get('AVF_FFMPEG')))\n")
    out = b.run_code(code, "env check", 60, sandbox=False)
    assert out["exit_code"] == 0, out["stderr"]
    assert "0.5 True" in out["stdout"] and "result.txt" in out["files_in_work_dir"]


def test_errors_and_timeouts_come_back(b):
    out = b.run_code("raise ValueError('boom')", "error", 30, sandbox=False)
    assert out["exit_code"] != 0 and "boom" in out["stderr"]
    out = b.run_code("import time; time.sleep(30)", "slow", 5, sandbox=False)
    assert out.get("timed_out")


@pytest.mark.skipif(platform.system() != "Darwin" or not os.path.isfile("/usr/bin/sandbox-exec"), reason="macOS sandbox only")
def test_macos_sandbox_protects_footage_and_network(b, tmp_path):
    footage = tmp_path / "footage"
    footage.mkdir()
    clip = footage / "C0001.MP4"
    clip.write_bytes(b"original")
    probe = os.path.join(os.path.expanduser("~"), f".avf_sandbox_probe_{uuid.uuid4().hex}")
    code = (
        "import os, socket\n"
        "open(os.path.join(os.environ['AVF_WORK'], 'ok.txt'), 'w').write('fine')\n"
        f"for path in [{str(clip)!r}, {probe!r}]:\n"
        "    try:\n"
        "        open(path, 'w').write('changed'); print('WROTE', path)\n"
        "    except Exception as e:\n"
        "        print('BLOCKED', type(e).__name__)\n"
        "s = socket.socket(); s.settimeout(3)\n"
        "try:\n"
        "    s.connect(('1.1.1.1', 80)); print('NET OPEN')\n"
        "except Exception as e:\n"
        "    print('NET BLOCKED', type(e).__name__)\n"
    )
    out = b.run_code(code, "sandbox check", 60, sandbox=True)
    assert out["sandboxed"] and out["exit_code"] == 0, out["stderr"]
    assert out["stdout"].count("BLOCKED") >= 3 and "WROTE" not in out["stdout"] and "NET OPEN" not in out["stdout"]
    assert clip.read_bytes() == b"original" and not os.path.exists(probe)
    assert "ok.txt" in out["files_in_work_dir"]


def test_launcher_script_mode(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("import sys; print('ran', sys.argv[1:])")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "launch_app.py"), "--avf-run-script", str(script), "x"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ran ['x']" in r.stdout
