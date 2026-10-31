{ ecadlib dialog for Altium Designer (written by `ecadlib altium-menu build`, ecadlib @VERSION@).

  One dark dialog for every menu entry, shown with OpenDialog(Mode, Project, MPN):
    Mode 'add'   ask for an MPN (unless one is given), build it, show where it came from, reinstall the library
    Mode 'bom'   check the project's BOM, show the counts, offer to fill the library, fill it
    Mode 'fill'  fill the library from the project's BOM straight away
  A part only an AI datasheet read can finish is offered as an AI step: the model and the cost first, the read only
  after a click (and a second yes above the limit), then the real cost next to the estimate.
  A held part can be released anyway (the Release anyway button, bottom left): the dialog shows why it is held, asks
  why it is fine, and ecadlib releases it like the review queue does (your name and reason kept in the plan).
  A timer polls the job folder (progress.ini while ecadlib runs, result.ini at the end).
  The dialog is modal, so Altium waits while it is open. Keep working (or the X) closes it while ecadlib works on in
  the background: the job goes on waiting.txt, and when it is done ecadlib calls FinishBackgroundJob in Altium, which
  opens the dialog on the result (OpenFinished) and puts the part into the library. If Altium was busy or closed then,
  the next menu entry offers it. }

Var
    Mode : String;
    Phase : String;
    Job : String;
    JobAction : String;
    Project : String;
    Title : String;
    Second : String;
    NextHint : String;
    Waited : Integer;
    Shown : Integer;
    Total : Integer;
    Swing : Integer;
    Dragging : Boolean;
    Busy : Boolean;         { the blue button says Working... / Installing...: no highlight, no hand }
    Seen : String;          { the progress.ini the dialog shows: Poll redraws only when it changes }
    RunHeight : Integer;    { the tallest the running view has been: it does not shrink while ecadlib works }
    DragX : Integer;
    DragY : Integer;

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
    C := $00E8E8E8;
    k := Pos('|', A);
    If k > 0 Then
    Begin
        B := Copy(A, k + 1, Length(A));
        A := Copy(A, 1, k - 1);
        k := Pos('|', B);
        If k > 0 Then
        Begin
            If Copy(B, k + 1, Length(B)) = '1' Then
                C := $005AB85C;
            B := Copy(B, 1, k - 1);
        End;
    End;
    If i = 1 Then
    Begin
        EcadlibDialog.Row1A.Caption := A;
        EcadlibDialog.Row1B.Caption := B;
        EcadlibDialog.Row1B.Font.Color := C;
        EcadlibDialog.Row1.Visible := V;
    End;
    If i = 2 Then
    Begin
        EcadlibDialog.Row2A.Caption := A;
        EcadlibDialog.Row2B.Caption := B;
        EcadlibDialog.Row2B.Font.Color := C;
        EcadlibDialog.Row2.Visible := V;
    End;
    If i = 3 Then
    Begin
        EcadlibDialog.Row3A.Caption := A;
        EcadlibDialog.Row3B.Caption := B;
        EcadlibDialog.Row3B.Font.Color := C;
        EcadlibDialog.Row3.Visible := V;
    End;
    If i = 4 Then
    Begin
        EcadlibDialog.Row4A.Caption := A;
        EcadlibDialog.Row4B.Caption := B;
        EcadlibDialog.Row4B.Font.Color := C;
        EcadlibDialog.Row4.Visible := V;
    End;
    If i = 5 Then
    Begin
        EcadlibDialog.Row5A.Caption := A;
        EcadlibDialog.Row5B.Caption := B;
        EcadlibDialog.Row5B.Font.Color := C;
        EcadlibDialog.Row5.Visible := V;
    End;
    If i = 6 Then
    Begin
        EcadlibDialog.Row6A.Caption := A;
        EcadlibDialog.Row6B.Caption := B;
        EcadlibDialog.Row6B.Font.Color := C;
        EcadlibDialog.Row6.Visible := V;
    End;
    If i = 7 Then
    Begin
        EcadlibDialog.Row7A.Caption := A;
        EcadlibDialog.Row7B.Caption := B;
        EcadlibDialog.Row7B.Font.Color := C;
        EcadlibDialog.Row7.Visible := V;
    End;
End;

Procedure ClearRows;
{ Also hides Release anyway: it belongs to one held result. }
Var
    i : Integer;
Begin
    For i := 1 To 7 Do
        SetRow(i, '');
    EcadlibDialog.ReleaseBtn.Visible := False;
End;

Function ButtonWidth(S : String) : Integer;
Begin
    Result := Length(S) * 7 + 30;
End;

Procedure Shade(Which : Integer; Level : Integer);
{ A button's colour: Which 1 = the blue one (Primary), 2 = Secondary, 3 = Release anyway; Level 0 = at rest,
  1 = under the mouse, 2 = held down. }
Var
    C : Integer;
Begin
    If Which = 1 Then
    Begin
        C := $00D9772F;
        If Busy Then
            C := $00705A4A
        Else If Level = 1 Then
            C := $00E8893F
        Else If Level = 2 Then
            C := $00B8621F;
        EcadlibDialog.Primary.Color := C;
        Exit;
    End;
    C := $00443E3E;
    If Level = 1 Then
        C := $00564F4F
    Else If Level = 2 Then
        C := $00363131;
    If Which = 2 Then
        EcadlibDialog.Secondary.Color := C
    Else
        EcadlibDialog.ReleaseBtn.Color := C;
End;

Procedure Buttons(Primary : String; Secondary : String; IsBusy : Boolean);
Begin
    Busy := IsBusy;
    EcadlibDialog.Primary.Caption := Primary;
    EcadlibDialog.Primary.Width := ButtonWidth(Primary);
    EcadlibDialog.Primary.Left := 420 - 16 - EcadlibDialog.Primary.Width;
    If Busy Then
        EcadlibDialog.Primary.Cursor := crDefault
    Else
        EcadlibDialog.Primary.Cursor := crHandPoint;
    Shade(1, 0);
    Shade(2, 0);
    Shade(3, 0);
    EcadlibDialog.Secondary.Caption := Secondary;
    EcadlibDialog.Secondary.Visible := Secondary <> '';
    EcadlibDialog.Secondary.Width := ButtonWidth(Secondary);
    EcadlibDialog.Secondary.Left := EcadlibDialog.Primary.Left - 8 - EcadlibDialog.Secondary.Width;
End;

Procedure PasteInto(Box : TEdit);
{ Altium takes Ctrl+V / Shift+Insert as its own paste, so the boxes paste here. }
Begin
    Box.PasteFromClipboard;
End;

Function LooksLikeMpn(S : String) : Boolean;
{ One word of 3 to 40 characters with a digit in it: a part number, not a sentence or a path someone copied. }
Var
    i : Integer;
Begin
    Result := False;
    If (Length(S) < 3) Or (Length(S) > 40) Then Exit;
    If (Pos(' ', S) > 0) Or (Pos(#13, S) > 0) Or (Pos(#10, S) > 0) Or (Pos(#9, S) > 0) Or (Pos('\', S) > 0) Then Exit;
    For i := 1 To Length(S) Do
        If (S[i] >= '0') And (S[i] <= '9') Then
            Result := True;
End;

Procedure ClipboardMpn;
{ A part number on the clipboard goes into the MPN box, selected, so typing replaces it. }
Begin
    PasteInto(EcadlibDialog.EditMpn);
    EcadlibDialog.EditMpn.Text := Trim(EcadlibDialog.EditMpn.Text);
    If LooksLikeMpn(EcadlibDialog.EditMpn.Text) Then
        EcadlibDialog.EditMpn.SelectAll
    Else
        EcadlibDialog.EditMpn.Text := '';
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
        Result := Result + 1 + ((k * 7) Div 380);
        S := Copy(S, k + 1, Length(S));
        k := Pos(#10, S);
    End;
    Result := Result + ((Length(S) * 7) Div 380);
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
    If EcadlibDialog.Row1.Visible Then
    Begin
        EcadlibDialog.Row1.Top := Y;
        Y := Y + 26;
    End;
    If EcadlibDialog.Row2.Visible Then
    Begin
        EcadlibDialog.Row2.Top := Y;
        Y := Y + 26;
    End;
    If EcadlibDialog.Row3.Visible Then
    Begin
        EcadlibDialog.Row3.Top := Y;
        Y := Y + 26;
    End;
    If EcadlibDialog.Row4.Visible Then
    Begin
        EcadlibDialog.Row4.Top := Y;
        Y := Y + 26;
    End;
    If EcadlibDialog.Row5.Visible Then
    Begin
        EcadlibDialog.Row5.Top := Y;
        Y := Y + 26;
    End;
    If EcadlibDialog.Row6.Visible Then
    Begin
        EcadlibDialog.Row6.Top := Y;
        Y := Y + 26;
    End;
    If EcadlibDialog.Row7.Visible Then
    Begin
        EcadlibDialog.Row7.Top := Y;
        Y := Y + 26;
    End;
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
        EcadlibDialog.Note.AutoSize := False;          { a word-wrapped label left to size itself shrinks to a column }
        EcadlibDialog.Note.Width := 380;
        EcadlibDialog.Note.Height := NoteLines(EcadlibDialog.Note.Caption) * 17;
        EcadlibDialog.Note.Top := Y + 6;
        Y := Y + EcadlibDialog.Note.Height + 10;
    End;
    If EcadlibDialog.Report.Visible Then
    Begin
        EcadlibDialog.Report.Top := Y + 2;
        Y := Y + 22;
    End;
    Y := Y + 10 + 30 + 16;
    If (Phase = 'run') Or (Phase = 'install') Then
    Begin
        If Y < RunHeight Then
            Y := RunHeight;         { rows coming and going would make the window and its buttons jump }
        RunHeight := Y;
    End;
    EcadlibDialog.Primary.Top := Y - 46;
    EcadlibDialog.Secondary.Top := Y - 46;
    EcadlibDialog.ReleaseBtn.Top := Y - 46;
    EcadlibDialog.ClientHeight := Y;
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
    EcadlibDialog.Note.Font.Color := $006060E0;
    EcadlibDialog.Note.Visible := True;
    If Job = '' Then
        Buttons('Close', '', False)
    Else
        Buttons('Close', 'Open log', False);
    Layout;
End;

Procedure ShowRunning(Act : String);
{ The dialog's live view of the job Job. }
Begin
    JobAction := Act;
    Phase := 'run';
    Waited := 0;
    Shown := 0;
    Total := 0;
    Swing := -60;
    Seen := '';
    RunHeight := 0;
    EcadlibDialog.Tick.Interval := 250;
    ClearRows;
    EcadlibDialog.AskBox.Visible := False;
    EcadlibDialog.Report.Visible := False;
    EcadlibDialog.Note.Visible := False;
    EcadlibDialog.Note.Font.Color := $009C9C9C;
    EcadlibDialog.Big.Visible := True;
    EcadlibDialog.Status.Visible := True;
    EcadlibDialog.Status.Caption := 'starting ecadlib';
    Meter(0, 0);
End;

Procedure Run(Act : String);
{ Starts the job Job (request.ini already written) and switches the dialog to its live view. }
Var
    Code : Integer;
Begin
    ShowRunning(Act);
    Ask(Job, 'title', Title);
    Second := 'background';
    Buttons('Working...', 'Keep working', True);
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

Procedure StartAi;
{ The AI step the last job offered: read the datasheets, held to the amount shown (result.ini ai_approve). }
Var
    Prev : String;
Begin
    Prev := Job;
    If Answer(Prev, 'ai_over') = '1' Then
        If Not ConfirmNoYes(Answer(Prev, 'ai_confirm')) Then
            Exit;
    SetTitle('Read with AI');
    EcadlibDialog.Big.Caption := 'Reading...';
    Job := StartJob('ai-read');
    Ask(Job, 'dialog', '1');
    Ask(Job, 'bom', Answer(Prev, 'ai_bom'));
    Ask(Job, 'mpn', Answer(Prev, 'ai_mpn'));
    Ask(Job, 'part', Answer(Prev, 'ai_part'));
    Ask(Job, 'approved_usd', Answer(Prev, 'ai_approve'));
    Ask(Job, 'over_limit_ok', Answer(Prev, 'ai_over'));
    Ask(Job, 'price_unknown_ok', Answer(Prev, 'ai_unknown'));
    Run('ai-read');
End;

Procedure ShowRelease;
{ The held result's Release anyway button, when ecadlib offered it (offer_release: the part can be released). }
Begin
    EcadlibDialog.ReleaseBtn.Visible := Answer(Job, 'offer_release') = '1';
    EcadlibDialog.ReleaseBtn.Width := ButtonWidth(EcadlibDialog.ReleaseBtn.Caption);
End;

Procedure AskRelease;
{ Release anyway, step 1: why the part is held, and what releasing means. Nothing is released yet. }
Var
    i : Integer;
    S : String;
Begin
    Phase := 'confirm_release';
    Second := 'back_release';
    S := Answer(Job, 'release_part') + ' is held because:';
    For i := 1 To 8 Do
        If Answer(Job, 'release_why' + IntToStr(i)) <> '' Then
            S := S + #13#10 + '- ' + Answer(Job, 'release_why' + IntToStr(i));
    If Answer(Job, 'release_more') <> '' Then
        S := S + #13#10 + '- and ' + Answer(Job, 'release_more') + ' more: release it from the review queue, which shows them all';
    S := S + #13#10 + 'Release anyway puts it into the library as it is. ecadlib keeps your name and your reason with it.';
    EcadlibDialog.Note.Caption := S;
    EcadlibDialog.Note.Font.Color := $009C9C9C;
    EcadlibDialog.Note.Visible := True;
    EcadlibDialog.ReleaseBtn.Visible := False;
    Buttons('Release it...', 'Back', False);
    Layout;
End;

Procedure StartRelease;
{ Release anyway, step 2: the person says why it is fine (a ready-made reason they can keep or change: one Enter);
  ecadlib releases it and puts it into the library. An emptied box releases nothing. }
Var
    Prev : String;
    Why : String;
Begin
    Prev := Job;
    Why := Trim(InputBox('ecadlib: release anyway', 'Why is ' + Answer(Prev, 'release_part') + ' fine as it is? (kept with your name in the library plan)', 'Checked it: fine as it is'));
    If Why = '' Then
        Exit;
    SetTitle('Release ' + Answer(Prev, 'release_part'));
    EcadlibDialog.Big.Caption := 'Releasing...';
    Job := StartJob('release');
    Ask(Job, 'dialog', '1');
    Ask(Job, 'bom', Answer(Prev, 'release_bom'));
    Ask(Job, 'mpn', Answer(Prev, 'release_mpn'));
    Ask(Job, 'part', Answer(Prev, 'release_part'));
    Ask(Job, 'release_key', Answer(Prev, 'release_key'));
    Ask(Job, 'reason', Why);
    Run('release');
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

Function OneLine(S : String; Room : Integer) : String;
{ The status line holds one line (about 54 characters at 7 px each in 380 px): longer text ends in '...' instead of
  being cut off mid-word by the label's edge. }
Begin
    Result := S;
    If Length(S) > Room Then
        Result := Copy(S, 1, Room - 3) + '...';
End;

Function Elapsed : String;
{ ' (12 s)' after the first few seconds, so a long step still shows the dialog is alive. }
Begin
    Result := '';
    If Waited >= 3000 Then
        Result := ' (' + IntToStr(Waited Div 1000) + ' s)';
End;

Procedure Poll;
{ Every tick: the meter moves and the seconds count; the rows and the layout are redrawn only when progress.ini says
  something new, so the window does not flicker. }
Var
    Ini : TIniFile;
    Done : Integer;
    Count : Integer;
    N : Integer;
    i : Integer;
    Said : String;
    Rows : String;
Begin
    Waited := Waited + EcadlibDialog.Tick.Interval;
    Ini := TIniFile.Create(Job + '\progress.ini');
    Done := Ini.ReadInteger('progress', 'done', 0);
    Count := Ini.ReadInteger('progress', 'total', 0);
    Said := Ini.ReadString('progress', 'text', 'starting ecadlib');
    N := Ini.ReadInteger('progress', 'rows', 0);
    If N > 7 Then
        N := 7;
    Rows := IntToStr(N);
    For i := 1 To N Do
        Rows := Rows + #10 + Ini.ReadString('progress', 'row' + IntToStr(i), '');
    Ini.Free;
    EcadlibDialog.Status.Caption := OneLine(Said, 46) + Elapsed;     { room for ' (123 s)' }
    Meter(Done, Count);
    If Rows <> Seen Then
    Begin
        Seen := Rows;
        Ini := TIniFile.Create(Job + '\progress.ini');
        For i := 1 To N Do
            SetRow(i, Ini.ReadString('progress', 'row' + IntToStr(i), ''));
        Ini.Free;
        Layout;
    End;
    If Waited >= SettingNumber('timeout_s', 1800) * 1000 Then
    Begin
        Waited := 0;
        EcadlibDialog.Tick.Enabled := False;      { no second question, and no Finish under the open question }
        If ConfirmNoYes('ecadlib: ' + Title + ' is still working. Keep waiting?') Or FileExists(Job + '\exit.txt') Then
            EcadlibDialog.Tick.Enabled := True
        Else
        Begin                                     { as Keep working: Altium puts it in when ecadlib finishes }
            MarkJob(Job, 'background.txt');
            AddWaiting(Job);
            Fail('Stopped waiting. ecadlib goes on in the background; Altium puts the result into the library when it is done.' + #13#10 + 'Next: look at its log, ' + Job + '\log.txt');
        End;
    End;
End;

Procedure Finish;
Var
    St : String;
    Headline : String;
    Extra : String;
    Ini : TIniFile;
Begin
    MarkJob(Job, 'finished.txt');      { a background job is put in once, here or by FinishBackgroundJob }
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
        If Answer(Job, 'big') <> '' Then
            EcadlibDialog.Big.Caption := Answer(Job, 'big');      { "Not found" for a bad part number }
        Exit;
    End;
    Extra := '';
    If Answer(Job, 'install') <> '' Then
    Begin
        { Altium does this itself and is busy until it is done: Keep working cannot help now, and clicks made
          meanwhile are taken (and ignored) before the result's buttons appear. }
        Phase := 'install';
        Second := '';
        If Answer(Job, 'build') <> '' Then
            EcadlibDialog.Status.Caption := 'Altium is busy building and installing the library'
        Else
            EcadlibDialog.Status.Caption := 'Altium is installing the library again';
        Buttons('Installing...', '', True);
        Layout;
        EcadlibDialog.Update;
        Application.ProcessMessages;
        Extra := Reinstall(Job);
        Application.ProcessMessages;
    End;
    Headline := Answer(Job, 'big');
    If Headline = '' Then
        Headline := 'Done';
    If BuildFailed(Extra) Then
        Headline := 'Altium could not build the library'
    Else If Pos('Not installed:', Extra) > 0 Then
        Headline := 'Not installed in Altium';
    EcadlibDialog.Big.Caption := Headline;
    ClearRows;
    Ini := TIniFile.Create(Job + '\result.ini');
    Total := Ini.ReadInteger('result', 'rows', 0);
    Ini.Free;
    If Total > 7 Then
        Total := 7;
    Shown := 0;
    EcadlibDialog.Tick.Interval := 70;      { the result rows come in quickly: the work is done, nobody waits for them }
    EcadlibDialog.Status.Visible := False;
    EcadlibDialog.MeterFill.Left := 0;
    EcadlibDialog.MeterFill.Width := EcadlibDialog.MeterTrack.Width;
    EcadlibDialog.Note.Font.Color := $009C9C9C;
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
        If Answer(Job, 'ai_cost_text') <> '' Then
            EcadlibDialog.Note.Caption := Answer(Job, 'ai_cost_text') + #13#10 + EcadlibDialog.Note.Caption;
        NextHint := '';
        If (Answer(Job, 'fix') <> '') And (St <> 'ready') Then
            NextHint := 'Next: ' + Answer(Job, 'fix');
        If Answer(Job, 'offer_ai') = '1' Then
        Begin
            Phase := 'offer_ai';
            { Not now goes back to the review-queue view (SecondaryClick), so the AI offer hides nothing for good. }
            If (St = 'held') Or (St = 'awaiting_approval') Or (Answer(Job, 'offer_queue') = '1') Then
                Second := 'after_ai';
            EcadlibDialog.Note.Caption := 'AI step: ' + Answer(Job, 'ai_text') + #13#10 + 'Nothing is sent until you click.';
            If NextHint <> '' Then
                EcadlibDialog.Note.Caption := EcadlibDialog.Note.Caption + #13#10 + NextHint;
            Buttons(Answer(Job, 'ai_button'), 'Not now', False);
        End
        Else If (St = 'held') Or (St = 'awaiting_approval') Or (Answer(Job, 'offer_queue') = '1') Then
        Begin
            Second := 'queue';
            Buttons('Done', 'Open review queue', False);
            ShowRelease;
        End
        Else
            Buttons('Done', '', False);
    End;
    If (Pos('Missing:', Extra) > 0) Or (Pos('Altium build:', Extra) > 0) Or (Pos('Not installed:', Extra) > 0) Then
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

Procedure KeepWorking;
{ Closes the dialog while ecadlib works on, so Altium is free: background.txt tells ecadlib_run.cmd to call Altium
  back (FinishBackgroundJob) when the job is done. A job that is done already is finished here, as usual. }
Begin
    MarkJob(Job, 'background.txt');
    AddWaiting(Job);
    If FileExists(Job + '\exit.txt') Then
        Exit;
    EcadlibDialog.Tick.Enabled := False;
    EcadlibDialog.ModalResult := mrCancel;
End;

Procedure OpenFinished(J : String);
{ A job Keep working left running is done: the dialog shows it as if it had waited (the timer runs Finish). }
Begin
    Job := J;
    Mode := 'finish';
    Project := Asked(J, 'project');
    Second := '';
    ClearRows;
    SetTitle(Asked(J, 'title'));
    EcadlibDialog.Big.Caption := Title;
    ShowRunning(Asked(J, 'action'));
    EcadlibDialog.Status.Caption := 'ecadlib is done';
    Buttons('Working...', '', True);
    Layout;
    EcadlibDialog.Tick.Enabled := True;
    EcadlibDialog.ShowModal;
    EcadlibDialog.Tick.Enabled := False;
End;

Procedure OpenDialog(AMode : String; AProject : String; AMpn : String);
Var
    J : String;
Begin
    J := DoneWaitingJob;
    If J <> '' Then
        If ConfirmNoYes('ecadlib finished ' + Asked(J, 'title') + ' while you kept working.' + #13#10 + 'Put it into the library now? (No skips it.)') Then
        Begin
            OpenFinished(J);
            Exit;
        End
        Else
            MarkJob(J, 'finished.txt');
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
            ClipboardMpn;
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
    Else If Phase = 'offer_ai' Then
        StartAi
    Else If Phase = 'confirm_release' Then
        StartRelease
    Else If Phase = 'done' Then
        ModalResult := mrOk;
End;

Procedure TEcadlibDialog.SecondaryClick(Sender : TObject);
Begin
    If Phase = 'install' Then
        Exit;
    If Second = 'queue' Then
    Begin
        Fire('queue', '', '');
        ModalResult := mrOk;
    End
    Else If Second = 'after_ai' Then
    Begin
        Phase := 'done';
        Second := 'queue';
        EcadlibDialog.Note.Caption := NextHint;
        EcadlibDialog.Note.Visible := NextHint <> '';
        Buttons('Done', 'Open review queue', False);
        ShowRelease;
        Layout;
    End
    Else If Second = 'back_release' Then
    Begin
        Phase := 'done';
        Second := 'queue';
        EcadlibDialog.Note.Caption := NextHint;
        EcadlibDialog.Note.Visible := NextHint <> '';
        Buttons('Done', 'Open review queue', False);
        Layout;
        ShowRelease;
    End
    Else If Second = 'log' Then
        RunApplication('notepad.exe "' + Job + '\log.txt"')
    Else If (Second = 'background') And (Phase = 'run') Then
        KeepWorking
    Else If Phase <> 'run' Then
        ModalResult := mrCancel;
End;

Procedure TEcadlibDialog.ReleaseClick(Sender : TObject);
Begin
    If Phase = 'done' Then
        AskRelease;
End;

Procedure TEcadlibDialog.ReportClick(Sender : TObject);
Begin
    Fire('open', 'path', Answer(Job, 'report'));
End;

{ The buttons' highlight (TPanel has none of its own): lighter under the mouse, darker while held. }
Procedure TEcadlibDialog.PrimaryEnter(Sender : TObject);
Begin
    Shade(1, 1);
End;

Procedure TEcadlibDialog.PrimaryLeave(Sender : TObject);
Begin
    Shade(1, 0);
End;

Procedure TEcadlibDialog.PrimaryDown(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
Begin
    Shade(1, 2);
End;

Procedure TEcadlibDialog.PrimaryUp(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
Begin
    Shade(1, 1);
End;

Procedure TEcadlibDialog.SecondaryEnter(Sender : TObject);
Begin
    Shade(2, 1);
End;

Procedure TEcadlibDialog.SecondaryLeave(Sender : TObject);
Begin
    Shade(2, 0);
End;

Procedure TEcadlibDialog.SecondaryDown(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
Begin
    Shade(2, 2);
End;

Procedure TEcadlibDialog.SecondaryUp(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
Begin
    Shade(2, 1);
End;

Procedure TEcadlibDialog.ReleaseEnter(Sender : TObject);
Begin
    Shade(3, 1);
End;

Procedure TEcadlibDialog.ReleaseLeave(Sender : TObject);
Begin
    Shade(3, 0);
End;

Procedure TEcadlibDialog.ReleaseDown(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
Begin
    Shade(3, 2);
End;

Procedure TEcadlibDialog.ReleaseUp(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
Begin
    Shade(3, 1);
End;

Procedure TEcadlibDialog.HeadCloseEnter(Sender : TObject);
Begin
    HeadClose.Color := $003A3AC4;
    HeadClose.Font.Color := $00FFFFFF;
End;

Procedure TEcadlibDialog.HeadCloseLeave(Sender : TObject);
Begin
    HeadClose.Color := $00242121;
    HeadClose.Font.Color := $009C9C9C;
End;

Procedure TEcadlibDialog.HeadMouseDown(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
{ The window has no Windows title bar (bsNone): its own header moves it. }
Begin
    Dragging := True;
    DragX := X;
    DragY := Y;
End;

Procedure TEcadlibDialog.HeadMouseMove(Sender : TObject; Shift : TShiftState; X, Y : Integer);
Begin
    If Dragging Then
    Begin
        Left := Left + X - DragX;
        Top := Top + Y - DragY;
    End;
End;

Procedure TEcadlibDialog.HeadMouseUp(Sender : TObject; Button : TMouseButton; Shift : TShiftState; X, Y : Integer);
Begin
    Dragging := False;
End;

Procedure TEcadlibDialog.EcadlibDialogKeyDown(Sender : TObject; Var Key : Word; Shift : TShiftState);
{ Ctrl+V/C/X/A and Shift+Insert in the MPN and manufacturer boxes (KeyPreview: the form sees keys first). }
Var
    Box : TEdit;
Begin
    If ActiveControl = EditMpn Then
        Box := EditMpn
    Else If ActiveControl = EditMaker Then
        Box := EditMaker
    Else
        Exit;
    If (InSet(ssCtrl, Shift) And (Key = 86)) Or (InSet(ssShift, Shift) And (Key = 45)) Then
    Begin
        Box.SelectAll;                { a paste replaces the box (it doubled the part number when appended) }
        PasteInto(Box);
    End
    Else If InSet(ssCtrl, Shift) And (Key = 67) Then Box.CopyToClipboard
    Else If InSet(ssCtrl, Shift) And (Key = 88) Then Box.CutToClipboard
    Else If InSet(ssCtrl, Shift) And (Key = 65) Then Box.SelectAll
    Else Exit;
    Key := 0;
End;

Procedure TEcadlibDialog.EcadlibDialogKeyPress(Sender : TObject; Var Key : Char);
{ The edit box would paste, copy or cut again on the Ctrl+V / C / X character KeyDown already handled. }
Begin
    If ((ActiveControl = EditMpn) Or (ActiveControl = EditMaker)) And ((Key = #22) Or (Key = #3) Or (Key = #24)) Then
        Key := #0;
End;

Procedure TEcadlibDialog.PasteMpnClick(Sender : TObject);
Begin
    EditMpn.SelectAll;
    PasteInto(EditMpn);
    EditMpn.SetFocus;
End;

Procedure TEcadlibDialog.EcadlibDialogClose(Sender : TObject; Var CloseAction : TCloseAction);
{ A closed form's enabled timer fires again whenever this script project runs (AD 26.10): stop it. Closed another
  way while it works (Alt+F4, the taskbar): the job still goes on waiting.txt, as with Keep working. While Altium
  installs the part it stays open: an Alt+F4 pressed during the build would otherwise hide the result. }
Begin
    If Phase = 'install' Then
    Begin
        CloseAction := caNone;
        Exit;
    End;
    Tick.Enabled := False;
    If (Phase = 'run') And (Second = 'background') Then
        If Not FileExists(Job + '\background.txt') Then
        Begin
            MarkJob(Job, 'background.txt');
            AddWaiting(Job);
        End;
End;

Procedure TEcadlibDialog.CloseClick(Sender : TObject);
Begin
    If Phase = 'install' Then
        Exit;               { Altium is putting the part in: closing now would hide whether it got there }
    If (Phase = 'run') And (Second = 'background') Then
    Begin
        KeepWorking;                { the X while it works: the same as Keep working }
        Exit;
    End;
    ModalResult := mrCancel;
End;
