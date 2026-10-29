"""The ecadlib dialog in Altium stays calm and responsive while ecadlib works (ecadlib_dialog.pas): Poll redraws only
what changed, the running window does not shrink and jump, the status counts seconds, the install step says Altium is
busy and ignores clicks, the timeout question stops the timer, and the result rows come in quickly.
python3 -m tests.test_altium_dialog_responsive. Nothing here runs Altium: the .pas is checked as text."""
import re

from ecadlib import altium_forms as AF, altium_menu as AM, altium_native as AN


def _routine(text, name):
    m = re.search(rf"(?ms)^(Procedure|Function) {re.escape(name)}\b.*?^End;", text)
    assert m, name
    return m.group(0)


PAS = AF.dialog_pas_text()


def test_poll_redraws_rows_only_when_progress_changes():
    poll = _routine(PAS, "Poll")
    assert "If Rows <> Seen Then" in poll and "Seen := Rows" in poll
    redraw = poll[poll.index("If Rows <> Seen Then"):]
    redraw = redraw[:redraw.index("End;")]
    assert "SetRow(" in redraw and "Layout" in redraw          # rows and layout only inside the change branch
    before = poll[:poll.index("If Rows <> Seen Then")]
    assert "SetRow(" not in before and "Layout" not in before
    assert "Meter(Done, Count)" in before                       # the meter still moves every tick
    assert "Seen := ''" in _routine(PAS, "ShowRunning")          # a new job draws its first progress


def test_status_counts_seconds_after_a_few():
    el = _routine(PAS, "Elapsed")
    assert "Waited >= 3000" in el and "Waited Div 1000" in el
    assert "Said + Elapsed" in _routine(PAS, "Poll")


def test_running_window_does_not_shrink():
    lay = _routine(PAS, "Layout")
    assert "If Y < RunHeight Then" in lay and "RunHeight := Y" in lay
    assert "(Phase = 'run') Or (Phase = 'install')" in lay
    # buttons sit at the bottom of whatever height the window keeps
    assert "Primary.Top := Y - 46" in lay and "ClientHeight := Y" in lay
    assert "RunHeight := 0" in _routine(PAS, "ShowRunning")


def test_install_step_is_shown_busy_and_ignores_clicks():
    fin = _routine(PAS, "Finish")
    i = fin.index("Extra := Reinstall(Job)")
    before = fin[:i]
    assert "Phase := 'install'" in before and "Buttons('Installing...', '', True)" in before
    assert "Second := ''" in before and "Altium is busy" in before
    assert before.rindex("Application.ProcessMessages") > before.index("Buttons('Installing...'")
    # clicks queued while Altium built the libraries are taken while still 'install', before the result's buttons
    after = fin[i:]
    assert after.index("Application.ProcessMessages") < after.index("Phase := 'done'")
    for h in ("TEcadlibDialog.SecondaryClick", "TEcadlibDialog.CloseClick"):
        body = _routine(PAS, h)
        assert body.index("If Phase = 'install' Then") < body.index("ModalResult"), h
    assert "Phase = 'install'" not in _routine(PAS, "TEcadlibDialog.PrimaryClick")
    close = _routine(PAS, "TEcadlibDialog.EcadlibDialogClose")     # Alt+F4 / taskbar Close pressed during the build
    assert close.index("CloseAction := caNone") < close.index("Tick.Enabled := False")   # it does nothing then anyway


def test_timeout_question_stops_the_timer_and_respects_a_finished_job():
    poll = _routine(PAS, "Poll")
    q = poll.index("ConfirmNoYes(")
    assert poll.rindex("Tick.Enabled := False", 0, q) > poll.index("Waited >= SettingNumber")
    assert "Or FileExists(Job + '\\exit.txt')" in poll[q:]      # done meanwhile: finish it, do not say "Stopped"
    assert "Tick.Enabled := True" in poll[q:]


def test_result_rows_come_in_quickly_and_the_next_run_polls_at_250():
    fin = _routine(PAS, "Finish")
    assert re.search(r"Tick\.Interval := (\d+);", fin) and int(re.search(r"Tick\.Interval := (\d+);", fin).group(1)) <= 100
    assert "Tick.Interval := 250" in _routine(PAS, "ShowRunning")


def test_dialog_unit_still_checks_clean():
    files = {AM.UNIT: AM.pas_text(), AF.DIALOG_UNIT: PAS, AF.DIALOG_FORM: AF.dialog_dfm_text(), AN.UNIT: AN.pas_text()}
    assert AM.project_problems(files) == []
    assert "Update" in AF.FORM_API                                 # EcadlibDialog.Update before the long install


def test_buttons_light_up_under_the_mouse_and_darken_when_held():
    dfm = AF.dialog_dfm_text()
    for panel, prefix in (("Primary", "Primary"), ("Secondary", "Secondary"), ("ReleaseBtn", "Release")):
        body = dfm[dfm.index(f"object {panel}: TPanel"):]
        body = body[:body.index("\r\n  end\r\n")]
        for e in ("Enter", "Leave", "Down", "Up"):
            assert f"OnMouse{e} = {prefix}{e}" in body, (panel, e)
            assert f"Procedure TEcadlibDialog.{prefix}{e}(" in PAS
    shade = _routine(PAS, "Shade")
    for c in (AF.BLUE, AF.BLUE_HOT, AF.BLUE_DOWN, AF.BLUE_OFF, AF.BTN, AF.BTN_HOT, AF.BTN_DOWN):
        assert c in shade, c
    assert shade.index("If Busy Then") < shade.index(AF.BLUE_HOT)         # a busy button does not light up
    btn = _routine(PAS, "Buttons")
    assert "Busy := IsBusy" in btn and "crDefault" in btn and all(f"Shade({i}, 0)" in btn for i in (1, 2, 3))
    assert "OnMouseEnter = HeadCloseEnter" in dfm and AF.CLOSE_HOT in _routine(PAS, "TEcadlibDialog.HeadCloseEnter")


def test_header_shows_the_small_logo():
    import struct
    dfm = AF.dialog_dfm_text()
    head = dfm[dfm.index("object Head: TPanel"):dfm.index("object AskBox: TPanel")]
    assert "object Logo: TImage" in head and f"Width = {AF.LOGO}" in head and AF.LOGO <= 20
    hexs = "".join(re.search(r"Picture\.Data = \{([0-9A-F\s]+)\}", head).group(1).split())
    data = bytes.fromhex(hexs)
    assert data[:8] == b"\x07TBitmap"
    size = int.from_bytes(data[8:12], "little")
    bmp = data[12:]
    assert size == len(bmp) and bmp[:2] == b"BM" and struct.unpack_from("<I", bmp, 2)[0] == len(bmp)
    w, h, _, bpp = struct.unpack_from("<iiHH", bmp, 18)
    assert (w, h, bpp) == (AF.LOGO, AF.LOGO, 24)
    # the title moves right to make room, and the logo drags the window like the rest of the header
    assert re.search(r"object HeadTitle: TLabel\r\n\s+Left = (\d+)", head).group(1) == str(12 + AF.LOGO + 8)
    logo = head[head.index("object Logo"):]
    assert "OnMouseDown = HeadMouseDown" in logo


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
