"""Keep working: the ecadlib dialog in Altium can close while a job runs, so Altium is free, and the running Altium is
called back to put the result into the library (ecadlib_dialog.pas KeepWorking / OpenFinished, ecadlib_menu.pas
FinishBackgroundJob / DoneWaitingJob, `ecadlib altium-menu wake`). python3 -m tests.test_altium_keep_working.
Nothing here runs Altium: the .pas is checked as text, wake() with a fake X2 and a fake clock."""
import re
from pathlib import Path

from ecadlib import altium_forms as AF, altium_menu as AM
from tests.fixtures_lite import lite


def _routine(text, name):
    m = re.search(rf"(?ms)^(Procedure|Function) {name}\b.*?^End;", text)
    assert m, name
    return m.group(0)


def _job(tmp_path, background=True):
    here = tmp_path / "menu"
    job = here / "jobs" / "20261020-020000-1"
    job.mkdir(parents=True)
    for n in (AM.PROJECT, AM.UNIT):
        (here / n).write_text("x")
    if background:
        (job / "background.txt").write_text("background.txt")
    return here, job


def test_dialog_offers_keep_working_while_it_runs():
    pas = AF.dialog_pas_text()
    run = _routine(pas, "Run")
    assert "Buttons('Working...', 'Keep working', True)" in run and "Second := 'background'" in run
    assert "Ask(Job, 'title', Title)" in run                         # OpenFinished shows the same title later
    keep = _routine(pas, "KeepWorking")
    # background.txt before the exit.txt check: ecadlib_run.cmd writes exit.txt before it looks for background.txt,
    # so a job that ends meanwhile is finished by one side or both (finished.txt makes the second a no-op)
    assert keep.index("MarkJob(Job, 'background.txt')") < keep.index("FileExists(Job + '\\exit.txt')")
    assert "AddWaiting(Job)" in keep and "ModalResult := mrCancel" in keep and "Tick.Enabled := False" in keep
    assert "KeepWorking" in _routine(pas, "TEcadlibDialog.SecondaryClick")
    assert "KeepWorking" in _routine(pas, "TEcadlibDialog.CloseClick")     # the X does the same, no question
    assert "MarkJob(Job, 'finished.txt')" in _routine(pas, "Finish")
    closed = _routine(pas, "TEcadlibDialog.EcadlibDialogClose")       # Alt+F4 while it works: not lost either
    assert "AddWaiting(Job)" in closed and "Not FileExists(Job + '\\background.txt')" in closed


def test_finished_background_job_reopens_the_dialog():
    pas, menu = AF.dialog_pas_text(), AM.pas_text()
    fin = _routine(pas, "OpenFinished")
    assert "ShowRunning(Asked(J, 'action'))" in fin and "Tick.Enabled := True" in fin and "ShowModal" in fin
    assert "Project := Asked(J, 'project')" in fin                     # Fill the library now needs it after a BOM check
    assert "OpenFinished(J)" in _routine(menu, "FinishBackgroundJob")
    opened = _routine(pas, "OpenDialog")                               # Altium closed or busy then: the next entry offers it
    assert opened.index("DoneWaitingJob") < opened.index("Mode := AMode")
    done = _routine(menu, "DoneWaitingJob")
    assert "finished.txt" in done and "exit.txt" in done and "DeleteFile(Here + 'waiting.txt')" in done


def test_units_have_no_problems():
    from ecadlib import altium_native as AN
    files = {AM.UNIT: AM.pas_text(), AF.DIALOG_UNIT: AF.dialog_pas_text(), AF.DIALOG_FORM: AF.dialog_dfm_text(),
             AN.UNIT: AN.pas_text()}
    assert AM.project_problems(files) == [], AM.project_problems(files)


def test_runner_calls_wake_after_exit():
    cmd = AM.run_cmd_text()
    lines = cmd.split("\r\n")
    ex = next(i for i, l in enumerate(lines) if l.startswith('> "%JOB%\\exit.txt" echo %ERRORLEVEL%'))
    assert lines[ex + 2] == 'if exist "%JOB%\\background.txt" %ECADLIB% altium-menu wake "%JOB%" > "%JOB%\\wake.txt" 2>&1'


def _long(monkeypatch):
    monkeypatch.setattr(AM, "_short_path", lambda p: p)                # no 8.3 names, on any OS


@lite
def test_wake_calls_the_running_altium_until_it_takes_the_job(tmp_path, monkeypatch):
    _long(monkeypatch)
    here, job = _job(tmp_path)
    calls, ticks = [], []

    def start(cmd):
        calls.append(cmd)
        if len(calls) == 2:                                            # the first call was not taken
            (job / "finished.txt").write_text("finished.txt")

    res = AM.wake(job, x2="C:\\AD26\\X2.EXE", busy=lambda h: False, start=start, sleep=ticks.append, wait_s=3)
    assert res["status"] == "ok" and len(calls) == 2 and len(ticks) == 4, (res, calls, ticks)
    assert calls[0] == ["C:\\AD26\\X2.EXE", f"-RScriptingSystem:RunScript(ProjectName={here / AM.PROJECT}|"
                                            f"ProcName={here / AM.UNIT}>FinishBackgroundJob)"]


@lite
def test_wake_waits_while_a_dialog_is_open_in_altium(tmp_path, monkeypatch):
    """A call while Altium shows a modal dialog leaves Altium's "busy" box and a hung X2.EXE on the screen: wait."""
    _long(monkeypatch)
    here, job = _job(tmp_path)
    calls, ticks, state = [], [], {"n": 0}

    def busy(hwnd):
        state["n"] += 1
        return state["n"] <= 3                                         # the dialog closes after three looks

    def start(cmd):
        calls.append(cmd)
        (job / "finished.txt").write_text("finished.txt")

    res = AM.wake(job, x2="X2.EXE", busy=busy, start=start, sleep=ticks.append, every_s=5)
    assert res["status"] == "ok" and len(calls) == 1 and ticks[:3] == [5, 5, 5], (res, ticks)
    calls.clear()
    (job / "finished.txt").unlink()
    res = AM.wake(job, x2="X2.EXE", busy=lambda h: True, start=calls.append, sleep=lambda s: None, patience_s=20)
    assert res["status"] == "waiting" and "dialog" in res["message"] and calls == []


def test_wake_folder_is_short_but_file_names_are_long(monkeypatch=None):
    """AD 26.10 finds neither ECADLI~1.PRJ nor a unit ECADLI~1.PAS: only the folder may be an 8.3 name."""
    real = AM._short_path
    try:
        AM._short_path = lambda p: Path("C:/ECADLI~1/menu")
        cmd = AM.wake_command("X2.EXE", Path("C:/ecadlib test/menu"))
    finally:
        AM._short_path = real
    assert cmd[1] == (f"-RScriptingSystem:RunScript(ProjectName={Path('C:/ECADLI~1/menu') / AM.PROJECT}|"
                      f"ProcName={Path('C:/ECADLI~1/menu') / AM.UNIT}>FinishBackgroundJob)")


@lite
def test_wake_gives_up_quietly(tmp_path, monkeypatch):
    _long(monkeypatch)
    here, job = _job(tmp_path)
    calls = []
    res = AM.wake(job, x2="X2.EXE", busy=lambda h: False, start=calls.append, sleep=lambda s: None, calls=3, wait_s=2)
    assert res["status"] == "waiting" and "did not take" in res["message"] and len(calls) == 3
    res = AM.wake(job, x2="", start=calls.append, sleep=lambda s: None)          # Altium is closed: never start one
    assert res["status"] == "waiting" and "not running" in res["message"] and len(calls) == 3
    (job / "background.txt").unlink()
    assert AM.wake(job, x2="X2.EXE", start=calls.append)["status"] == "skipped" and len(calls) == 3


@lite
def test_wake_never_puts_a_space_on_altiums_command_line(tmp_path, monkeypatch):
    """A script path with a space makes Altium start a second, unlicensed copy: such a folder waits for the menu."""
    _long(monkeypatch)                                                 # as when the folder has no 8.3 name
    here, job = _job(tmp_path / "my menu")
    assert AM.wake_command("X2.EXE", here) is None
    calls = []
    res = AM.wake(job, x2="X2.EXE", start=calls.append, sleep=lambda s: None)
    assert res["status"] == "waiting" and calls == []


def test_wake_cli():
    assert "wake" in AM.__doc__ and "FinishBackgroundJob" in AM.__doc__

