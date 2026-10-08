"""The launcher owns the pipe: the minter gets the write end, the browser script the read end."""

import shutil
import sys

import pytest

from scripts.identity_capture import capture_session

SENTINEL = "SENTINEL-TOKEN-NOT-A-REAL-JWT"
NODE = shutil.which("node") or "node"

FAKE_MINTER = (
    "import os, stat, sys\n"
    "fd = int(sys.argv[sys.argv.index('--token-fd') + 1])\n"
    "assert stat.S_ISFIFO(os.fstat(fd).st_mode) and fd >= 3\n"
    f"os.write(fd, b'{SENTINEL}')\n"
    "os.close(fd)\n"
)
FAKE_READER = (
    "import os, stat, sys\n"
    "fd = int(sys.argv[sys.argv.index('--token-fd') + 1])\n"
    "assert stat.S_ISFIFO(os.fstat(fd).st_mode)\n"
    "print('read', len(os.read(fd, 4096)), 'bytes', 'args', sys.argv[1:3])\n"
)


def test_the_reader_gets_the_tokens_over_the_pipe_and_nothing_else_sees_them(capfd):
    code = capture_session.run(
        ["https://site.example.net", "out"],
        minter_cmd=[sys.executable, "-c", FAKE_MINTER],
        reader_cmd=[sys.executable, "-c", FAKE_READER],
    )

    seen = capfd.readouterr()
    assert code == 0
    assert f"read {len(SENTINEL)} bytes" in seen.out
    assert SENTINEL not in seen.out and SENTINEL not in seen.err


@pytest.mark.skipif(not shutil.which(NODE), reason="node 22 is not installed here")
def test_a_real_node_process_can_read_the_inherited_pipe(capfd):
    script = (
        "const fs=require('node:fs');const i=process.argv.indexOf('--token-fd');"
        "const fd=Number(process.argv[i+1]);"
        "console.log('node', fs.fstatSync(fd).isFIFO(), fs.readFileSync(fd,'utf8').length);"
    )
    code = capture_session.run(
        [],
        minter_cmd=[sys.executable, "-c", FAKE_MINTER],
        reader_cmd=[NODE, "-e", script, "--"],
    )

    seen = capfd.readouterr()
    assert code == 0
    assert f"node true {len(SENTINEL)}" in seen.out
    assert SENTINEL not in seen.out + seen.err


def test_a_failed_minter_fails_the_run_and_the_reader_sees_an_empty_pipe(capfd):
    code = capture_session.run(
        [],
        minter_cmd=[sys.executable, "-c", "import sys; sys.exit(1)"],
        reader_cmd=[sys.executable, "-c", "import os,sys;"
                    "fd=int(sys.argv[sys.argv.index('--token-fd')+1]);"
                    "print('empty', os.read(fd, 10) == b'')"],
    )

    assert code != 0
    assert "empty True" in capfd.readouterr().out


def test_check_runs_only_the_minter_in_check_mode_and_starts_no_browser(capfd):
    minter = (
        "import sys\n"
        "assert '--check' in sys.argv and '--token-fd' in sys.argv\n"
        "print('minter checked')\n"
    )

    code = capture_session.main(
        ["--check"],
        minter_cmd=[sys.executable, "-c", minter],
        reader_cmd=[sys.executable, "-c", "raise SystemExit('the reader must not start')"],
    )

    assert code == 0
    assert "minter checked" in capfd.readouterr().out


def test_the_default_commands_point_at_the_minter_and_the_capture_script():
    assert capture_session.default_minter_cmd()[1].endswith("identity_capture/mint_session.py")
    assert capture_session.default_reader_cmd()[1].endswith("identity_capture/capture.mjs")
