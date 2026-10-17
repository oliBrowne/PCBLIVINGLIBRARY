"""The ecadlib dialog inside Altium Designer: a DelphiScript form (.pas + .dfm) the menu's entries open.

It looks like Altium's own dark dialogs and does the work in place, as the landing page's workflow shows it:

    ecadlib . Check project BOM      a big count ("24 lines"), one row per kind (already in your library, a source can
                                     supply, needs a footprint), a progress line, then Later / Fill the library now
    ecadlib . Add part from MPN      MPN + manufacturer, then the lookup live (where ecadlib looked, what it took, the
                                     release check), then Done; the Components panel shows the part after the reinstall
    ecadlib . Fill the library       the librarian's progress part by part, then ready / held counts

The dialog never waits in a loop: a TTimer (every 250 ms) reads <job>/progress.ini while ecadlib runs and
<job>/result.ini when exit.txt appears, so Altium keeps painting. Only a step that needs a browser opens one: the
report of a BOM check (on request) and the review queue for a held part. Everything the dialog needs from the menu
unit (StartJob, Ask, Answer, CommandLine, Reinstall, Setting...) is called across units, as Altium script projects
allow (Altium-Designer-addons OpenLib calls its libUtils unit the same way).

Event handlers are named in the .dfm (OnClick = PrimaryClick): DelphiScript cannot assign one to a control in code
(Altium-Designer-addons ReturnViaCheck.pas: "CheckBox.OnClick := ... throws error")."""
from . import __version__

DIALOG_UNIT, DIALOG_FORM = "ecadlib_dialog.pas", "ecadlib_dialog.dfm"
ROWS = 7
WIDTH = 420
LABEL_W = 190             # the rows' left column; the value column gets the rest (Consolas: about 7 px a character)

# Altium's dark UI, as Delphi TColor values ($00BBGGRR)
BG, HEAD, TEXT, DIM, RULE = "$002E2A2A", "$00242121", "$00E8E8E8", "$009C9C9C", "$00433D3D"
BLUE, BLUE_OFF, GREEN, RED, BTN, FIELD = "$00D9772F", "$00705A4A", "$005AB85C", "$006060E0", "$00443E3E", "$00201D1D"

# Every identifier the dialog's .pas uses besides its own routines, the menu unit's and the .dfm's component names:
# VCL properties, methods and constants of the script form components (Altium "Forms & Components":
# altium.com/documentation/altium-designer/scripting/delphiscript/forms-components; TR0120 p10-11).
FORM_API = {
    "Caption": "TLabel / TPanel / TForm caption", "Visible": "TControl.Visible", "Top": "TControl.Top",
    "Left": "TControl.Left", "Width": "TControl.Width", "Height": "TControl.Height", "ClientHeight": "TForm.ClientHeight",
    "Font": "TControl.Font", "Color": "TControl.Color / TFont.Color", "Text": "TEdit.Text", "Enabled": "TTimer.Enabled",
    "Interval": "TTimer.Interval", "ModalResult": "TForm.ModalResult (Forms & Components: ShowModal example)",
    "ShowModal": "TForm.ShowModal (Forms & Components)", "mrOk": "TModalResult", "mrCancel": "TModalResult",
    "SetFocus": "TWinControl.SetFocus", "SelectAll": "TEdit.SelectAll",
    "EcadlibDialog": "the form instance a script form unit gets (named after the .dfm's object)",
    "Sender": "event handler parameter", "TObject": "event handler parameter type", "Style": "TFont.Style",
}


def _dlg_pas():
    rows_set = "\n".join(f"""    If i = {i} Then
    Begin
        EcadlibDialog.Row{i}A.Caption := A;
        EcadlibDialog.Row{i}B.Caption := B;
        EcadlibDialog.Row{i}B.Font.Color := C;
        EcadlibDialog.Row{i}.Visible := V;
    End;""" for i in range(1, ROWS + 1))
    rows_top = "\n".join(f"""    If EcadlibDialog.Row{i}.Visible Then
    Begin
        EcadlibDialog.Row{i}.Top := Y;
        Y := Y + 26;
    End;""" for i in range(1, ROWS + 1))
    return r"""{ ecadlib dialog for Altium Designer (written by `ecadlib altium-menu build`, ecadlib @VERSION@).

  One dark dialog for every menu entry, shown with OpenDialog(Mode, Project, MPN):
    Mode 'add'   ask for an MPN (unless one is given), build it, show where it came from, reinstall the library
    Mode 'bom'   check the project's BOM, show the counts, offer to fill the library, fill it
    Mode 'fill'  fill the library from the project's BOM straight away
  A timer polls the job folder (progress.ini while ecadlib runs, result.ini at the end), so Altium never freezes. }

Var
    Mode : String;
    Phase : String;
    Job : String;
    JobAction : String;
    Project : String;
    Title : String;
    Second : String;
    Waited : Integer;
    Shown : Integer;
    Total : Integer;
    Swing : Integer;

Procedure SetRow(i : Integer; Spec : String);
{ Spec is "label|value|ok" (ok = 1: the value in green); '' hides the row. }
Var
    A : String;
    B : String;
    C : Integer;
    V : Boolean;
    k : Integer;
Begin
    V := Spec <> '';
    A := Spec;
    B := '';
    C := @TEXT@;
    k := Pos('|', A);
    If k > 0 Then
    Begin
        B := Copy(A, k + 1, Length(A));
        A := Copy(A, 1, k - 1);
        k := Pos('|', B);
        If k > 0 Then
        Begin
            If Copy(B, k + 1, Length(B)) = '1' Then
                C := @GREEN@;
            B := Copy(B, 1, k - 1);
        End;
    End;
@ROWS_SET@
End;

Procedure ClearRows;
Var
    i : Integer;
Begin
    For i := 1 To @ROWS@ Do
        SetRow(i, '');
End;

Function ButtonWidth(S : String) : Integer;
Begin
    Result := Length(S) * 7 + 30;
End;

Procedure Buttons(Primary : String; Secondary : String; Busy : Boolean);
Begin
    EcadlibDialog.Primary.Caption := Primary;
    EcadlibDialog.Primary.Width := ButtonWidth(Primary);
    EcadlibDialog.Primary.Left := @WIDTH@ - 16 - EcadlibDialog.Primary.Width;
    If Busy Then
        EcadlibDialog.Primary.Color := @BLUE_OFF@
    Else
        EcadlibDialog.Primary.Color := @BLUE@;
    EcadlibDialog.Secondary.Caption := Secondary;
    EcadlibDialog.Secondary.Visible := Secondary <> '';
    EcadlibDialog.Secondary.Width := ButtonWidth(Secondary);
    EcadlibDialog.Secondary.Left := EcadlibDialog.Primary.Left - 8 - EcadlibDialog.Secondary.Width;
End;

Function NoteLines(S : String) : Integer;
{ How many lines the note wraps to (a word-wrapped TLabel with AutoSize would shrink to its first word). }
Var
    k : Integer;
Begin
    Result := 1;
    k := Pos(#10, S);
    While k > 0 Do
    Begin
        Result := Result + 1 + ((k * 7) Div @NOTE_W@);
        S := Copy(S, k + 1, Length(S));
        k := Pos(#10, S);
    End;
    Result := Result + ((Length(S) * 7) Div @NOTE_W@);
End;

Procedure Layout;
Var
    Y : Integer;
Begin
    Y := 46;
    If EcadlibDialog.AskBox.Visible Then
    Begin
        EcadlibDialog.AskBox.Top := Y;
        Y := Y + EcadlibDialog.AskBox.Height + 4;
    End;
    If EcadlibDialog.Big.Visible Then
    Begin
        EcadlibDialog.Big.Top := Y;
        Y := Y + 40;
    End;
@ROWS_TOP@
    If EcadlibDialog.Status.Visible Then
    Begin
        EcadlibDialog.Status.Top := Y + 4;
        Y := Y + 24;
    End;
    If EcadlibDialog.MeterTrack.Visible Then
    Begin
        EcadlibDialog.MeterTrack.Top := Y + 4;
        Y := Y + 12;
    End;
    If EcadlibDialog.Note.Visible Then
    Begin
        EcadlibDialog.Note.Height := NoteLines(EcadlibDialog.Note.Caption) * 17;
        EcadlibDialog.Note.Top := Y + 6;
        Y := Y + EcadlibDialog.Note.Height + 10;
    End;
    If EcadlibDialog.Report.Visible Then
    Begin
        EcadlibDialog.Report.Top := Y + 2;
        Y := Y + 22;
    End;
    EcadlibDialog.Primary.Top := Y + 10;
    EcadlibDialog.Secondary.Top := Y + 10;
    EcadlibDialog.ClientHeight := Y + 10 + 30 + 16;
End;

Procedure Meter(Done : Integer; Count : Integer);
{ Count = 0: ecadlib does not know how far it is; a short bar swings along the track. }
Begin
    EcadlibDialog.MeterTrack.Visible := True;
    If Count > 0 Then
    Begin
        EcadlibDialog.MeterFill.Left := 0;
        EcadlibDialog.MeterFill.Width := (EcadlibDialog.MeterTrack.Width * Done) Div Count;
    End
    Else
    Begin
        Swing := Swing + 16;
        If Swing > EcadlibDialog.MeterTrack.Width Then
            Swing := -60;
        EcadlibDialog.MeterFill.Left := Swing;
        EcadlibDialog.MeterFill.Width := 60;
    End;
End;

Procedure SetTitle(S : String);
Begin
    Title := S;
    EcadlibDialog.HeadTitle.Caption := 'ecadlib ' + #183 + ' ' + S;
End;

Procedure Fail(Problem : String);
Begin
    Phase := 'done';
    Second := 'log';
    EcadlibDialog.Big.Caption := 'Not done';
    EcadlibDialog.Status.Visible := False;
    EcadlibDialog.MeterTrack.Visible := False;
    EcadlibDialog.Note.Caption := Problem;
    EcadlibDialog.Note.Font.Color := @RED@;
    EcadlibDialog.Note.Visible := True;
    If Job = '' Then
        Buttons('Close', '', False)
    Else
        Buttons('Close', 'Open log', False);
    Layout;
End;

Procedure Run(Act : String);
{ Starts the job Job (request.ini already written) and switches the dialog to its live view. }
Var
    Code : Integer;
Begin
    JobAction := Act;
    Phase := 'run';
    Waited := 0;
    Shown := 0;
    Total := 0;
    Swing := -60;
    ClearRows;
    EcadlibDialog.AskBox.Visible := False;
    EcadlibDialog.Report.Visible := False;
    EcadlibDialog.Note.Visible := False;
    EcadlibDialog.Note.Font.Color := @DIM@;
    EcadlibDialog.Big.Visible := True;
    EcadlibDialog.Status.Visible := True;
    EcadlibDialog.Status.Caption := 'starting ecadlib';
    Meter(0, 0);
    Buttons('Working...', '', True);
    Layout;
    Code := RunApplication(CommandLine(Job));
    If Code <> 0 Then
        Fail('Windows could not start ecadlib (error ' + IntToStr(Code) + ').' + #13#10 + 'Next: set hidden=0 in ' + Here + 'ecadlib_menu.ini and try again.')
    Else
        EcadlibDialog.Tick.Enabled := True;
End;

Procedure StartAdd;
Var
    MPN : String;
    Maker : String;
Begin
    MPN := Trim(EcadlibDialog.EditMpn.Text);
    Maker := Trim(EcadlibDialog.EditMaker.Text);
    If MPN = '' Then
    Begin
        EcadlibDialog.EditMpn.SetFocus;
        Exit;
    End;
    SetTitle(MPN);
    EcadlibDialog.Big.Caption := MPN;
    Job := StartJob('add-part');
    Ask(Job, 'dialog', '1');
    Ask(Job, 'mpn', MPN);
    Ask(Job, 'manufacturer', Maker);
    Run('add-part');
End;

Procedure StartBom;
Begin
    SetTitle('Check project BOM');
    EcadlibDialog.Big.Caption := 'Checking...';
    Job := StartJob('check-bom');
    Ask(Job, 'dialog', '1');
    Ask(Job, 'project', Project);
    Run('check-bom');
End;

Procedure StartFill;
Begin
    SetTitle('Fill the library');
    EcadlibDialog.Big.Caption := 'Filling...';
    Job := StartJob('fill');
    Ask(Job, 'dialog', '1');
    Ask(Job, 'project', Project);
    Run('fill');
End;

Procedure Poll;
Var
    Ini : TIniFile;
    Done : Integer;
    Count : Integer;
    N : Integer;
    i : Integer;
Begin
    Waited := Waited + EcadlibDialog.Tick.Interval;
    Ini := TIniFile.Create(Job + '\progress.ini');
    Done := Ini.ReadInteger('progress', 'done', 0);
    Count := Ini.ReadInteger('progress', 'total', 0);
    EcadlibDialog.Status.Caption := Ini.ReadString('progress', 'text', EcadlibDialog.Status.Caption);
    N := Ini.ReadInteger('progress', 'rows', 0);
    If N > @ROWS@ Then
        N := @ROWS@;
    For i := 1 To N Do
        SetRow(i, Ini.ReadString('progress', 'row' + IntToStr(i), ''));
    Ini.Free;
    Meter(Done, Count);
    Layout;
    If Waited >= SettingNumber('timeout_s', 1800) * 1000 Then
    Begin
        Waited := 0;
        If Not ConfirmNoYes('ecadlib: ' + Title + ' is still working. Keep waiting?') Then
            Fail('Stopped waiting. ecadlib may still finish in the background.' + #13#10 + 'Next: look at its log, ' + Job + '\log.txt');
    End;
End;

Procedure Finish;
Var
    St : String;
    Headline : String;
    Extra : String;
    Ini : TIniFile;
Begin
    If Not FileExists(Job + '\result.ini') Then
    Begin
        If FileExists(Job + '\notfound.txt') Then
            Fail('ecadlib is not installed on this PC (not on PATH, and no Python can import it).' + #13#10 + 'Next: pip install ecadlib, or set command= in ' + Here + 'ecadlib_menu.ini')
        Else
            Fail('ecadlib stopped without a result.' + #13#10 + 'Next: look at its log, ' + Job + '\log.txt');
        Exit;
    End;
    St := Answer(Job, 'status');
    If St = 'error' Then
    Begin
        Fail(MessageText(Job, ''));
        Exit;
    End;
    Extra := '';
    If Answer(Job, 'install') <> '' Then
    Begin
        EcadlibDialog.Status.Caption := 'installing the library again';
        Application.ProcessMessages;
        Extra := Reinstall(Job);
    End;
    Headline := Answer(Job, 'big');
    If Headline = '' Then
        Headline := 'Done';
    EcadlibDialog.Big.Caption := Headline;
    ClearRows;
    Ini := TIniFile.Create(Job + '\result.ini');
    Total := Ini.ReadInteger('result', 'rows', 0);
    Ini.Free;
    If Total > @ROWS@ Then
        Total := @ROWS@;
    Shown := 0;
    EcadlibDialog.Status.Visible := False;
    EcadlibDialog.MeterFill.Left := 0;
    EcadlibDialog.MeterFill.Width := EcadlibDialog.MeterTrack.Width;
    EcadlibDialog.Note.Font.Color := @DIM@;
    EcadlibDialog.Note.Caption := '';
    Second := '';
    Phase := 'done';
    If JobAction = 'check-bom' Then
    Begin
        EcadlibDialog.Report.Visible := Answer(Job, 'report') <> '';
        If Answer(Job, 'warnings') <> '' Then
            EcadlibDialog.Note.Caption := Answer(Job, 'warnings');
        If Answer(Job, 'offer_fill') = '1' Then
        Begin
            Phase := 'offer';
            Buttons('Fill the library now', 'Later', False);
        End
        Else
            Buttons('Done', '', False);
    End
    Else
    Begin
        If (Answer(Job, 'fix') <> '') And (St <> 'ready') Then
            EcadlibDialog.Note.Caption := 'Next: ' + Answer(Job, 'fix');
        If (St = 'held') Or (St = 'awaiting_approval') Or (Answer(Job, 'offer_queue') = '1') Then
        Begin
            Second := 'queue';
            Buttons('Done', 'Open review queue', False);
        End
        Else
            Buttons('Done', '', False);
    End;
    If Pos('Missing:', Extra) > 0 Then
        EcadlibDialog.Note.Caption := EcadlibDialog.Note.Caption + Extra;
    EcadlibDialog.Note.Visible := EcadlibDialog.Note.Caption <> '';
    Layout;
End;

Procedure Reveal;
{ The result rows come in one by one, like the progress log on ecadlib's page. }
Var
    Ini : TIniFile;
Begin
    Shown := Shown + 1;
    Ini := TIniFile.Create(Job + '\result.ini');
    SetRow(Shown, Ini.ReadString('result', 'row' + IntToStr(Shown), ''));
    Ini.Free;
    Layout;
End;

Procedure Fire(Act : String; Key : String; Value : String);
{ A job the dialog does not wait for: open the review queue or a report in the browser. }
Var
    J : String;
Begin
    J := StartJob(Act);
    If Key <> '' Then
        Ask(J, Key, Value);
    RunApplication(CommandLine(J));
End;

Procedure OpenDialog(AMode : String; AProject : String; AMpn : String);
Begin
    Mode := AMode;
    Project := AProject;
    Job := '';
    JobAction := '';
    Second := '';
    ClearRows;
    EcadlibDialog.Tick.Enabled := False;
    EcadlibDialog.Note.Visible := False;
    EcadlibDialog.Report.Visible := False;
    EcadlibDialog.Status.Visible := False;
    EcadlibDialog.MeterTrack.Visible := False;
    EcadlibDialog.Big.Visible := True;
    If AMode = 'add' Then
    Begin
        SetTitle('Add part from MPN');
        EcadlibDialog.EditMpn.Text := AMpn;
        EcadlibDialog.EditMaker.Text := '';
        If AMpn <> '' Then
            StartAdd
        Else
        Begin
            Phase := 'ask';
            EcadlibDialog.AskBox.Visible := True;
            EcadlibDialog.Big.Visible := False;
            Buttons('OK', 'Cancel', False);
            Layout;
        End;
    End
    Else If AMode = 'fill' Then
        StartFill
    Else
        StartBom;
    EcadlibDialog.ShowModal;
    EcadlibDialog.Tick.Enabled := False;
End;

Procedure TEcadlibDialog.TickTimer(Sender : TObject);
Begin
    If Phase = 'run' Then
    Begin
        If FileExists(Job + '\exit.txt') Then
        Begin
            Tick.Enabled := False;
            Finish;
            Tick.Enabled := True;
        End
        Else
            Poll;
    End
    Else If Shown < Total Then
        Reveal
    Else
        Tick.Enabled := False;
End;

Procedure TEcadlibDialog.PrimaryClick(Sender : TObject);
Begin
    If Phase = 'ask' Then
        StartAdd
    Else If Phase = 'offer' Then
        StartFill
    Else If Phase = 'done' Then
        ModalResult := mrOk;
End;

Procedure TEcadlibDialog.SecondaryClick(Sender : TObject);
Begin
    If Second = 'queue' Then
    Begin
        Fire('queue', '', '');
        ModalResult := mrOk;
    End
    Else If Second = 'log' Then
        RunApplication('notepad.exe "' + Job + '\log.txt"')
    Else If Phase <> 'run' Then
        ModalResult := mrCancel;
End;

Procedure TEcadlibDialog.ReportClick(Sender : TObject);
Begin
    Fire('open', 'path', Answer(Job, 'report'));
End;

Procedure TEcadlibDialog.EcadlibDialogClose(Sender : TObject; Var CloseAction : TCloseAction);
{ A closed form's enabled timer fires again whenever this script project runs (AD 26.10): stop it. }
Begin
    Tick.Enabled := False;
End;

Procedure TEcadlibDialog.CloseClick(Sender : TObject);
Begin
    If Phase = 'run' Then
        If Not ConfirmNoYes('ecadlib is still working on ' + Title + '. Stop waiting?' + #13#10 + 'It finishes in the background; run the menu entry again to put the result into the library.') Then
            Exit;
    ModalResult := mrCancel;
End;
""".replace("@ROWS_SET@", rows_set).replace("@ROWS_TOP@", rows_top).replace("@ROWS@", str(ROWS)) \
        .replace("@WIDTH@", str(WIDTH)).replace("@TEXT@", TEXT).replace("@GREEN@", GREEN).replace("@RED@", RED) \
        .replace("@DIM@", DIM).replace("@NOTE_W@", str(WIDTH - 40)).replace("@BLUE@", BLUE).replace("@BLUE_OFF@", BLUE_OFF).replace("@VERSION@", __version__)


def _font(color=TEXT, size=-12, face="Segoe UI", bold=False, ind="      "):
    return (f"{ind}Font.Charset = DEFAULT_CHARSET\n{ind}Font.Color = {color}\n{ind}Font.Height = {size}\n"
            f"{ind}Font.Name = '{face}'\n{ind}Font.Style = [{'fsBold' if bold else ''}]\n{ind}ParentFont = False\n")


def _dfm_str(s):
    """A .dfm string: printable ASCII in quotes, anything else as #code (183 = middle dot, 215 = multiplication sign)."""
    out, quoted = "", False
    for ch in s:
        if 32 <= ord(ch) < 127 and ch != "'":
            out += ch if quoted else "'" + ch
            quoted = True
        else:
            out += ("'" if quoted else "") + f"#{ord(ch)}"
            quoted = False
    return (out + "'") if quoted else (out or "''")


def _label(name, left, top, width, height, caption, ind="    ", align="", wrap=False, **font):
    i2 = ind + "  "
    extra = (f"{i2}Alignment = taRightJustify\n" if align == "r" else "") + (f"{i2}WordWrap = True\n" if wrap else "")
    return (f"{ind}object {name}: TLabel\n{i2}Left = {left}\n{i2}Top = {top}\n{i2}Width = {width}\n{i2}Height = {height}\n"
            f"{i2}AutoSize = False\n{i2}Caption = {_dfm_str(caption)}\n{_font(ind=i2, **font)}"
            f"{i2}Transparent = True\n{extra}{ind}end\n")


def _panel(name, left, top, width, height, color, body="", caption="", ind="  ", onclick="", font=None, visible=True):
    i2 = ind + "  "
    return (f"{ind}object {name}: TPanel\n{i2}Left = {left}\n{i2}Top = {top}\n{i2}Width = {width}\n{i2}Height = {height}\n"
            f"{i2}BevelOuter = bvNone\n{i2}Caption = {_dfm_str(caption)}\n{i2}Color = {color}\n{i2}ParentBackground = False\n"
            + (_font(ind=i2, **font) if font else "") + (f"{i2}Cursor = crHandPoint\n{i2}OnClick = {onclick}\n" if onclick else "")
            + ("" if visible else f"{i2}Visible = False\n") + body + f"{ind}end\n")


def _edit(name, left, top, width, ind="      "):
    i2 = ind + "  "
    return (f"{ind}object {name}: TEdit\n{i2}Left = {left}\n{i2}Top = {top}\n{i2}Width = {width}\n{i2}Height = 24\n"
            f"{i2}BorderStyle = bsSingle\n{i2}Color = {FIELD}\n{_font(ind=i2)}{i2}Text = ''\n{ind}end\n")


def dialog_dfm_text():
    """The form: header with title and close mark, the MPN box, the big line, ROWS rows, status, meter, note, report
    link and two buttons. Positions are set again by Layout at run time; these are the first frame's."""
    w = WIDTH - 32
    rows = "".join(_panel(f"Row{i}", 16, 90 + 26 * i, w, 26, BG, visible=False, body=(
        _label(f"Row{i}A", 0, 4, LABEL_W, 18, "", face="Consolas") +
        _label(f"Row{i}B", LABEL_W, 4, w - LABEL_W, 18, "", align="r", face="Consolas") +
        _panel(f"Row{i}Rule", 0, 25, w, 1, RULE, ind="    "))) for i in range(1, ROWS + 1))
    ask = _panel("AskBox", 16, 46, w, 104, BG, visible=False, body=(
        _label("AskMpn", 0, 0, w, 16, "MPN", color=DIM) + _edit("EditMpn", 0, 18, w) +
        _label("AskMaker", 0, 52, w, 16, "Manufacturer (optional)", color=DIM) + _edit("EditMaker", 0, 70, w)))
    meter = _panel("MeterTrack", 16, 300, w, 2, RULE, visible=False,
                   body=_panel("MeterFill", 0, 0, 0, 2, BLUE, ind="    "))
    button_font = {"color": "$00FFFFFF"}
    head = _panel("Head", 0, 0, WIDTH, 34, HEAD, body=(
        _label("HeadTitle", 14, 8, WIDTH - 60, 18, "ecadlib · Check project BOM") +
        _panel("HeadClose", WIDTH - 34, 2, 30, 30, HEAD, caption="×", ind="    ", onclick="CloseClick",
               font={"color": DIM, "size": -16})))
    return f"""object EcadlibDialog: TEcadlibDialog
  Left = 0
  Top = 0
  BorderStyle = bsNone
  Caption = 'ecadlib'
  ClientHeight = 300
  ClientWidth = {WIDTH}
  Color = {BG}
{_font(ind="  ")}  FormKind = fkModal
  KeyPreview = True
  OldCreateOrder = False
  OnClose = EcadlibDialogClose
  Position = poMainFormCenter
  PixelsPerInch = 96
  TextHeight = 15
{head}{ask}{_label("Big", 16, 46, w, 34, "", size=-24)}{rows}{_label("Status", 16, 250, w, 18, "", color=DIM)}{meter}{_label("Note", 16, 310, w, 18, "", wrap=True, color=DIM)}  object Report: TLabel
    Left = 16
    Top = 330
    Width = 160
    Height = 18
    AutoSize = False
    Caption = 'Open the full report'
    Cursor = crHandPoint
{_font(color=BLUE, ind="    ")}    Transparent = True
    Visible = False
    OnClick = ReportClick
  end
{_panel("Secondary", 120, 350, 80, 30, BTN, caption="Later", onclick="SecondaryClick", font=button_font)}{_panel("Primary", 208, 350, 136, 30, BLUE, caption="OK", onclick="PrimaryClick", font=button_font)}  object EnterKey: TButton
    Left = -200
    Top = 0
    Width = 10
    Height = 10
    Caption = 'OK'
    Default = True
    TabOrder = 9
    OnClick = PrimaryClick
  end
  object EscKey: TButton
    Left = -200
    Top = 20
    Width = 10
    Height = 10
    Cancel = True
    Caption = 'Cancel'
    TabOrder = 10
    OnClick = CloseClick
  end
  object Tick: TTimer
    Enabled = False
    Interval = 250
    OnTimer = TickTimer
    Left = 4
    Top = 4
  end
end
""".replace("\n", "\r\n")


def dialog_pas_text():
    return _dlg_pas().replace("\n", "\r\n")


def dfm_names(dfm):
    """Component names declared in a .dfm (object Name: TClass), the form's included."""
    import re
    return re.findall(r"(?m)^\s*object (\w+): \w+", dfm)
