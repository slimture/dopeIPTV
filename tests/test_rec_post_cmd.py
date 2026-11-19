"""The command after a recording, when Windows runs it through cmd.exe.

Placeholders are filled in per argument and no shell is asked - but Windows
itself puts cmd.exe in front of a batch file (.bat, .cmd), and cmd.exe
reads its arguments as a shell does. A title is a provider's words:

    after.bat {title}      with the title    x" & calc & "

was the batch file and then calc. And a recording's file name - made from
the title - with an & and no space in it was not even in quotes.
"""

from __future__ import annotations

import subprocess

import pytest

from dopeiptv.core import rec_extras

ENV = {"PATH": r"C:\Windows", "USERPROFILE": r"C:\Users\me", "ComSpec": r"C:\Windows\cmd.exe"}
PATH = r"C:\Rec\Nyheterna 2026-11-19 21.00.ts"


@pytest.mark.parametrize("program, through", [
    ("after.bat", True), ("AFTER.CMD", True), (r"C:\My Scripts\after.bat", True),
    ("C:/scripts/after.cmd", True), ("cmd", True), ("CMD.EXE", True),
    (r"C:\Windows\System32\cmd.exe", True), ("comskip.exe", False), ("python", False),
    ("after.bat.exe", False), ("powershell", False), ("", False)])
def test_which_programs_windows_runs_through_cmd(program, through):
    assert rec_extras.through_cmd(program) is through


def _line(command, title="Nyheterna", channel="SVT1", path=PATH):
    out = rec_extras.post_command(command, path, title, channel, windows=True, env=ENV)
    assert isinstance(out, str)
    return out


def test_a_title_cannot_end_its_argument_or_start_a_command():
    line = _line("after.bat {title}", 'x" & calc & "')
    # One argument, in quotes, with nothing left that ends them.
    assert line == 'after.bat "x & calc & "'
    assert line.count('"') == 2
    line = _line('after.bat "{channel}: {title}"', 'a"b', 'c"d|e')
    assert line == 'after.bat "cd|e: ab"'
    # A new line is a new command to cmd.exe: gone.
    assert _line("after.bat {title}", "x\r\ncalc") == "after.bat xcalc"


@pytest.mark.parametrize("name", ["A&calc&B", "A|B", "A^B", "(A)", "A<B>", "100%", "Wow!"])
def test_every_argument_cmd_reads_is_in_quotes(name):
    """An argument with a space was always in quotes; one without was not,
    whatever was in it."""
    path = rf"C:\Rec\{name}.ts"
    line = _line("after.bat {path} --name={path}", path=path)
    assert line == f'after.bat "{path}" "--name={path}"'
    # Without a character cmd.exe reads, nothing changes: as before.
    assert _line("after.bat {path} done", path=r"C:\Rec\A.ts") == r"after.bat C:\Rec\A.ts done"
    assert _line("after.bat {path}") == f'after.bat "{PATH}"'


def test_a_variables_name_in_a_title_is_not_expanded():
    """%NAME% is expanded inside quotes too: of a variable there is, the
    marks go (%CMDCMDLINE% holds quotes of its own)."""
    assert _line("after.bat {title}", "x%CMDCMDLINE%y") == "after.bat xCMDCMDLINEy"
    assert _line("after.bat {title}", "%path%") == "after.bat path"
    assert _line("after.bat {title}", "%USERPROFILE:~0,1%") == "after.bat USERPROFILE:~0,1"
    # No variable by that name: as written (cmd.exe leaves it too).
    assert _line("after.bat {title}", "100% Wolf") == 'after.bat "100% Wolf"'
    assert _line("after.bat {title}", "50% off, 20% more") == 'after.bat "50% off, 20% more"'
    assert _line("after.bat {title}", "Mamma Mia!") == 'after.bat "Mamma Mia!"'


def test_a_file_name_cmd_would_expand_is_not_handed_over_at_all():
    """The file's own name cannot be changed: the command is not run for
    that recording (and says so in the log)."""
    for path in (r"C:\Rec\x %CMDCMDLINE% y.ts", r"C:\Rec\%PATH%.ts", r"C:\Rec\a !path! b.ts"):
        with pytest.raises(ValueError):
            rec_extras.post_command("after.bat {path}", path, "t", windows=True, env=ENV)
    # A title next to a channel's name may not make one either.
    with pytest.raises(ValueError):
        rec_extras.post_command("after.bat {title}{channel}", PATH, "a%PA", "TH%b",
                                windows=True, env=ENV)
    # What the person wrote themselves may name a variable.
    line = _line(r"after.bat %USERPROFILE%\out {path}")
    assert line == rf'after.bat "%USERPROFILE%\out" "{PATH}"'


def test_cmd_itself_as_the_program_the_same():
    assert _line("cmd /c after.bat {title}", 'x" & calc') == 'cmd /c after.bat "x & calc"'


def test_any_other_program_gets_its_arguments_as_before():
    """Not through cmd.exe: the list, untouched - a quote in a title is
    text to a program that reads its own arguments."""
    for windows in (True, False):
        out = rec_extras.post_command("comskip.exe {title} {path}", PATH, 'x" & calc & "',
                                      windows=windows, env=ENV)
        assert out == ["comskip.exe", 'x" & calc & "', PATH]
    # A batch file's name means nothing off Windows.
    assert rec_extras.post_command("after.bat {title}", "/r/a.ts", 'x" & y', windows=False) \
        == ["after.bat", 'x" & y']


def test_the_quoting_is_subprocess_own_for_an_argument_with_a_space():
    """Python quotes an argument with a space in it; the same quoting, for
    one with a character cmd.exe reads."""
    for arg in ("a b", "C:\\dir with space\\", 'say "hi" now', "", "trailing\\ ", "a\tb",
                "back\\\\slashes and \\\"quote"):
        assert rec_extras._quoted(arg) == subprocess.list2cmdline([arg])
    # Backslashes before the closing quote are doubled, as Windows' programs read them.
    assert rec_extras._quoted("C:\\Rec\\A&B\\") == '"C:\\Rec\\A&B\\\\"'


def test_run_post_starts_the_line_for_a_batch_file_on_windows(monkeypatch):
    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env") or {}

    monkeypatch.setattr(rec_extras.os, "name", "nt")
    monkeypatch.setattr(rec_extras.subprocess, "Popen", FakeProc)
    assert rec_extras.run_post("after.bat {title} {path}", r"C:\Rec\A&B.ts", 'x" & calc & "',
                               'SVT"1') is not None
    assert seen["cmd"] == r'after.bat "x & calc & " "C:\Rec\A&B.ts"'
    # What a batch file reads with %NAME% is read on by cmd.exe: the same words.
    assert seen["env"]["DOPEIPTV_REC_TITLE"] == "x & calc & "
    assert seen["env"]["DOPEIPTV_REC_CHANNEL"] == "SVT1"
    assert seen["env"]["DOPEIPTV_REC_PATH"] == r"C:\Rec\A&B.ts"
    # A file name that would be expanded: not started.
    seen.clear()
    monkeypatch.setenv("SECRETVAR", "1")
    assert rec_extras.run_post("after.bat {path}", r"C:\Rec\%SECRETVAR%.ts", "t") is None
    assert seen == {}
