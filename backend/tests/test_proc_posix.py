import os
from pathlib import Path

import pytest

from app.tools.proc import run_argv


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_timeout_kills_child_not_us():
    r = await run_argv(["sleep", "30"], Path("."), timeout_s=1)
    assert r.timed_out
