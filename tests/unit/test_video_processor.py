"""
Regression tests for ffmpeg subprocess handling.

Why this exists: `subprocess.run` forked from a threadpool worker deadlocks the child
before exec under the live FastAPI server (fine standalone, hangs in-process). The
processor now shells out via asyncio.create_subprocess_exec (event-loop child watcher)
and no longer runs a blocking `ffmpeg -version` probe at construction time.
"""

import asyncio
import sys

import pytest

from src.infrastructure.video.processor import FFmpegVideoProcessor, _run_ffmpeg


def test_init_does_not_probe_ffmpeg():
    """Construction must not shell out — even a bogus binary path is fine.

    Guards the removal of the blocking `ffmpeg -version` check that deadlocked in-process.
    """
    proc = FFmpegVideoProcessor(
        ffmpeg_path="definitely-not-a-real-binary-xyz",
        ffprobe_path="also-not-real-xyz",
    )
    assert proc._ffmpeg == "definitely-not-a-real-binary-xyz"


def test_run_ffmpeg_executes_via_event_loop():
    """_run_ffmpeg runs a child through the loop and returns (rc, stdout, stderr) as bytes."""
    cmd = [sys.executable, "-c", "import sys; sys.stdout.write('ok'); sys.stderr.write('e')"]
    rc, out, err = asyncio.run(_run_ffmpeg(cmd, timeout=10))
    assert rc == 0
    assert out == b"ok"
    assert err == b"e"


def test_run_ffmpeg_times_out_and_kills_child():
    """A child that overruns the timeout raises asyncio.TimeoutError (and is killed)."""
    cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_run_ffmpeg(cmd, timeout=0.5))
