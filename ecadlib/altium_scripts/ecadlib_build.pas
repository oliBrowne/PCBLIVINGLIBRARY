{ ecadlib: build libraries inside Altium Designer (written by `ecadlib altium-native write`).

  BuildLibrariesInAltium   reads ecadlib_build.txt next to this script project and, for every library listed in it,
                           makes a new PcbLib / SchLib document, creates each footprint / symbol with Altium's own
                           object factories, and saves it over the file named there (the old file is kept as
                           NAME.ecadlib_old until the new one is saved, and put back when the build fails). Libraries that are open are
                           closed first (unsaved changes in them are lost: they would be overwritten anyway).
                           Writes ecadlib_build_report.txt: one line per library, OK or FAILED.
  BuildLibraries(Folder)   the same for the ecadlib_build.txt in Folder (other scripts call this).

  Units in the recipe: PCB lengths in Altium internal units (1 mil = 10000), schematic lengths in 1/1000 mil,
  angles in 1/1000 degree, layers as legacy layer numbers. }

Var
    EbF      : TStringList;     { fields of the current recipe line }
    EbLines  : TStringList;
    EbReport : TStringList;
    EbIdx    : Integer;
    EbErr    : String;      { set by a Make* routine that cannot build its primitive }

Procedure EbSplit(S : String);
Var
    i   : Integer;
    Cur : String;
Begin
    EbF.Clear;
    Cur := '';
    For i := 1 To Length(S) Do
    Begin
        If S[i] = #9 Then
        Begin
            EbF.Add(Cur);
            Cur := '';
        End
        Else
            Cur := Cur + S[i];
    End;
    EbF.Add(Cur);
End;

Function EbN(i : Integer) : Integer;
Begin
    Result := StrToInt(Trim(EbF[i]));
End;

Function EbAng(i : Integer) : Double;
Begin
    Result := StrToInt(Trim(EbF[i])) / 1000;
End;

Function EbSchC(i : Integer) : Integer;
Begin
    Result := MilsToCoord(StrToInt(Trim(EbF[i])) / 1000);
End;

Function PcbLayer(L : Integer) : Integer;
Begin
    Case L Of
        1  : Result := eTopLayer;
        32 : Result := eBottomLayer;
        33 : Result := eTopOverlay;
        34 : Result := eBottomOverlay;
        35 : Result := eTopPaste;
        36 : Result := eBottomPaste;
        37 : Result := eTopSolder;
        38 : Result := eBottomSolder;
        56 : Result := eKeepOutLayer;
        74 : Result := eMultiLayer;
    Else
        If (L >= 57) And (L <= 72) Then
            Result := LayerUtils.MechanicalLayer(L - 56)
        Else
            Result := L;
    End;
End;

Function PadShape(S : String) : Integer;
Begin
    If S = 'rect' Then Result := eRectangular
    Else If S = 'roundrect' Then Result := eRoundedRectangular
    Else If S = 'octagonal' Then Result := eOctagonal
    Else Result := eRounded;
End;

Function HoleShape(S : String) : Integer;
Begin
    If S = 'square' Then Result := eSquareHole
    Else If S = 'slot' Then Result := eSlotHole
    Else Result := eRoundHole;
End;

Procedure SkipBlock;
Begin
    While EbIdx < EbLines.Count Do
    Begin
        EbSplit(EbLines[EbIdx]);
        If EbF[0] = 'END' Then Break;
        Inc(EbIdx);
    End;
End;

Function MissingStep(Folder : String) : String;
{ The first STEP file a BODY line of the current block names that is not there ('' when all are). Checked before the
  old library is moved aside, so a library Altium cannot build is left on disk as it was. }
Var
    k : Integer;
Begin
    Result := '';
    k := EbIdx + 1;
    While k < EbLines.Count Do
    Begin
        EbSplit(EbLines[k]);
        If EbF[0] = 'END' Then Break;
        If (EbF[0] = 'BODY') And (EbF.Count > 4) Then
            If (EbF[4] <> '') And (Not FileExists(Folder + EbF[4])) Then
            Begin
                Result := 'STEP file missing: ' + Folder + EbF[4];
                Break;
            End;
        Inc(k);
    End;
End;

Procedure CloseIfOpen(Path : String);
Var
    Doc : IServerDocument;
Begin
    Doc := Client.GetDocumentByPath(Path);
    If Doc <> Nil Then
        Client.CloseDocument(Doc);
End;

{ The old library file is moved aside (Path + '.ecadlib_old') before Altium writes the new one, and removed only once
  the new one is on disk and not empty: a build that fails puts the old file back, so it never leaves the library
  missing. }
Function EbSize(Path : String) : Integer;
Var
    H : Integer;
Begin
    Result := 0;
    If Not FileExists(Path) Then Exit;
    H := FileOpen(Path, 64);                   { fmOpenRead or fmShareDenyNone }
    If H < 0 Then
    Begin
        Result := 1;                           { there, but it cannot be read now: not counted as missing }
        Exit;
    End;
    Result := FileSeek(H, 0, 2);
    FileClose(H);
End;

Function EbSetAside(Path : String) : String;
{ '' when Path can be written: the old file, if any, is now Path + '.ecadlib_old'. Else why not (Path is untouched). }
Var
    Bak : String;
Begin
    Result := '';
    Bak := Path + '.ecadlib_old';
    If FileExists(Bak) And Not FileExists(Path) Then
        RenameFile(Bak, Path);                 { left by a build that was cut short }
    If Not FileExists(Path) Then Exit;
    If FileExists(Bak) Then
        DeleteFile(Bak);
    If Not RenameFile(Path, Bak) Then
        Result := 'could not move the old file aside to ' + Bak + ' (is it open in another program?); the library file was left as it was';
End;

Function EbPutBack(Path : String) : String;
{ After a failed build: the old file back at Path. Says what happened ('' when there was no old file). }
Var
    Bak : String;
Begin
    Result := '';
    Bak := Path + '.ecadlib_old';
    CloseIfOpen(Path);
    If Not FileExists(Bak) Then Exit;
    If FileExists(Path) Then
        DeleteFile(Path);
    If RenameFile(Bak, Path) Then
        Result := ' (the previous library file was kept)'
    Else
        Result := ' (the previous library file is at ' + Bak + ')';
End;

Function EbFinish(Path : String; Kind : String; Done : String) : String;
{ The report line once Altium has saved Path: OK only when the file is there and not empty. }
Begin
    If EbSize(Path) > 0 Then
    Begin
        If FileExists(Path + '.ecadlib_old') Then
            DeleteFile(Path + '.ecadlib_old');
        Result := 'OK' + #9 + Kind + #9 + Path + #9 + Done;
    End
    Else
        Result := 'FAILED' + #9 + Kind + #9 + Path + #9 + 'Altium did not save the file' + EbPutBack(Path);
End;

{ ------------------------------------------------------------------ footprints }
Procedure AddPrim(Comp : IPCB_LibComponent; Prim : IPCB_Primitive);
Begin
    Comp.AddPCBObject(Prim);
    PCBServer.SendMessageToRobots(Comp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
End;

Function EbContour(First : Integer) : IPCB_Contour;
Var
    k, Cnt : Integer;
Begin
    Result := PCBServer.PCBContourFactory;
    Cnt := EbN(First);
    For k := 0 To Cnt - 1 Do
        Result.AddPoint(EbN(First + 1 + 2 * k), EbN(First + 2 + 2 * k));
End;

Procedure MakePad(Comp : IPCB_LibComponent);
Var
    Pad   : IPCB_Pad;
    Cache : TPadCache;
Begin
    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    Pad.Mode := ePadMode_Simple;
    Pad.Name := EbF[1];
    Pad.X := EbN(2);
    Pad.Y := EbN(3);
    Pad.TopXSize := EbN(4);
    Pad.TopYSize := EbN(5);
    Pad.MidXSize := EbN(4);
    Pad.MidYSize := EbN(5);
    Pad.BotXSize := EbN(4);
    Pad.BotYSize := EbN(5);
    Pad.TopShape := PadShape(EbF[7]);
    Pad.MidShape := PadShape(EbF[7]);
    Pad.BotShape := PadShape(EbF[7]);
    Pad.Rotation := EbAng(6);
    Pad.Layer := PcbLayer(EbN(9));
    If EbN(10) > 0 Then
    Begin
        Pad.HoleSize := EbN(10);
        Pad.HoleType := HoleShape(EbF[11]);
        If EbF[11] = 'slot' Then
            Pad.HoleWidth := EbN(12);
        Pad.HoleRotation := EbAng(13);
        Pad.Plated := EbN(14) = 1;
    End
    Else
        Pad.HoleSize := 0;
    If EbF[7] = 'roundrect' Then
    Begin
        Pad.SetState_StackCRPctOnLayer(eTopLayer, EbN(8));
        Pad.SetState_StackCRPctOnLayer(eMidLayer1, EbN(8));
        Pad.SetState_StackCRPctOnLayer(eBottomLayer, EbN(8));
    End;
    AddPrim(Comp, Pad);
    { mask expansions go through the pad cache: in Altium 26 PasteMaskExpansion / SolderMaskExpansion are read-only
      on the pad (assigning them crashes the script engine). Rule pads are set too: the first footprint of a session
      otherwise keeps an invalid cache and is saved with no mask expansion mode at all. }
    Cache := Pad.GetState_Cache;
    If EbF[15] = 'manual' Then
    Begin
        Cache.PasteMaskExpansion := EbN(16);
        Cache.PasteMaskExpansionValid := eCacheManual;
    End
    Else
        Cache.PasteMaskExpansionValid := eCacheValid;
    If EbF[17] = 'manual' Then
    Begin
        Cache.SolderMaskExpansion := EbN(18);
        Cache.SolderMaskExpansionValid := eCacheManual;
    End
    Else
        Cache.SolderMaskExpansionValid := eCacheValid;
    Pad.SetState_Cache(Cache);
End;

Procedure MakeTrack(Comp : IPCB_LibComponent);
Var
    T : IPCB_Track;
Begin
    T := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    T.X1 := EbN(1); T.Y1 := EbN(2); T.X2 := EbN(3); T.Y2 := EbN(4);
    T.Width := EbN(5);
    T.Layer := PcbLayer(EbN(6));
    AddPrim(Comp, T);
End;

Procedure MakeArc(Comp : IPCB_LibComponent);
Var
    A : IPCB_Arc;
Begin
    A := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
    A.XCenter := EbN(1); A.YCenter := EbN(2); A.Radius := EbN(3);
    A.StartAngle := EbAng(4); A.EndAngle := EbAng(5);
    A.LineWidth := EbN(6);
    A.Layer := PcbLayer(EbN(7));
    AddPrim(Comp, A);
End;

Procedure MakeFill(Comp : IPCB_LibComponent);
Var
    Fl : IPCB_Fill;
Begin
    Fl := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
    Fl.X1Location := EbN(1); Fl.Y1Location := EbN(2); Fl.X2Location := EbN(3); Fl.Y2Location := EbN(4);
    Fl.Rotation := 0;
    Fl.Layer := PcbLayer(EbN(5));
    AddPrim(Comp, Fl);
End;

Procedure MakeRegion(Comp : IPCB_LibComponent);
Var
    R : IPCB_Region;
Begin
    R := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    R.SetOutlineContour(EbContour(3));
    R.Layer := PcbLayer(EbN(1));
    If EbN(2) = 1 Then
        R.IsKeepout := True;
    AddPrim(Comp, R);
End;

Procedure MakeText(Comp : IPCB_LibComponent);
Var
    T : IPCB_Text;
Begin
    T := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
    T.UseTTFonts := False;
    T.Text := EbF[1];
    T.XLocation := EbN(2); T.YLocation := EbN(3);
    T.Size := EbN(4);
    T.Width := EbN(5);
    T.Rotation := EbAng(6);
    T.Layer := PcbLayer(EbN(7));
    AddPrim(Comp, T);
End;

Procedure MakeBody(Comp : IPCB_LibComponent; Folder : String);
Var
    B : IPCB_ComponentBody;
    M : IPCB_Model;
Begin
    B := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
    B.SetOutlineContour(EbContour(9));
    B.Layer := PcbLayer(EbN(1));
    B.BodyProjection := eBoardSide_Top;
    If EbF[4] <> '' Then
    Begin
        If Not FileExists(Folder + EbF[4]) Then
        Begin
            EbErr := 'STEP file missing: ' + Folder + EbF[4];
            Exit;
        End;
        M := B.ModelFactory_FromFilename(Folder + EbF[4], True);
        If M <> Nil Then
            M.SetState(EbAng(5), EbAng(6), EbAng(7), EbN(8));
    End
    Else
        M := B.ModelFactory_CreateExtruded(EbN(3), EbN(2), $808080);
    If M = Nil Then
    Begin
        EbErr := 'Altium made no 3D model for a body (' + EbF[4] + ')';
        Exit;
    End;
    B.Model := M;
    B.SetState_FromModel;
    AddPrim(Comp, B);
End;

Function BuildPcbLib(Folder : String) : String;
Var
    Path    : String;
    Doc     : IServerDocument;
    Lib     : IPCB_Library;
    Blank   : IPCB_LibComponent;
    Comp    : IPCB_LibComponent;
    Kind    : String;
    Count   : Integer;
Begin
    Path := Folder + EbF[1];
    EbErr := MissingStep(Folder);
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Result := 'FAILED' + #9 + 'PCBLIB' + #9 + Path + #9 + EbErr + ' (the library file was left as it was)';
        Exit;
    End;
    CloseIfOpen(Path);
    EbErr := EbSetAside(Path);
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Result := 'FAILED' + #9 + 'PCBLIB' + #9 + Path + #9 + EbErr;
        Exit;
    End;
    Doc := Client.OpenNewDocument('PCBLIB', Path, '', False);
    Client.ShowDocument(Doc);
    Lib := PCBServer.GetCurrentPCBLibrary;
    Blank := Lib.CurrentComponent;
    Comp := Nil;
    Count := 0;
    PCBServer.PreProcess;
    Inc(EbIdx);
    While EbIdx < EbLines.Count Do
    Begin
        EbSplit(EbLines[EbIdx]);
        Kind := EbF[0];
        If Kind = 'END' Then Break;
        If Kind = 'FOOTPRINT' Then
        Begin
            Comp := PCBServer.CreatePCBLibComp;
            Comp.Name := EbF[1];
            Comp.Description := EbF[2];
            Comp.Height := EbN(3);
            Lib.RegisterComponent(Comp);
            Inc(Count);
        End
        Else If Kind = 'PAD' Then MakePad(Comp)
        Else If Kind = 'TRACK' Then MakeTrack(Comp)
        Else If Kind = 'ARC' Then MakeArc(Comp)
        Else If Kind = 'FILL' Then MakeFill(Comp)
        Else If Kind = 'REGION' Then MakeRegion(Comp)
        Else If Kind = 'TEXT' Then MakeText(Comp)
        Else If Kind = 'BODY' Then MakeBody(Comp, Folder);
        If EbErr <> '' Then Break;
        Inc(EbIdx);
    End;
    PCBServer.PostProcess;
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Client.CloseDocument(Doc);
        Result := 'FAILED' + #9 + 'PCBLIB' + #9 + Path + #9 + EbErr + EbPutBack(Path);
        Exit;
    End;
    If (Blank <> Nil) And (Count > 0) Then
        Lib.RemoveComponent(Blank);
    If Comp <> Nil Then
        Lib.CurrentComponent := Comp;
    Doc.SetModified(True);
    Doc.DoFileSave('PCBLIB');
    Client.CloseDocument(Doc);
    Result := EbFinish(Path, 'PCBLIB', IntToStr(Count) + ' footprint(s)');
End;

{ ------------------------------------------------------------------ symbols }
Procedure Own(Comp : ISch_Component; O : ISch_GraphicalObject);
Begin
    O.OwnerPartId := EbN(1);
    O.OwnerPartDisplayMode := EbN(2);
    Comp.AddSchObject(O);
End;

Procedure Filled(O : ISch_GraphicalObject; Fill : String);
Begin
    O.Color := 128;
    If Fill = 'body' Then
    Begin
        O.AreaColor := 11599871;
        O.IsSolid := True;
    End
    Else If Fill = 'line' Then
    Begin
        O.AreaColor := 128;
        O.IsSolid := True;
    End
    Else
        O.IsSolid := False;
End;

Procedure Vertices(O : ISch_Polyline; First : Integer);
Var
    k, Cnt : Integer;
Begin
    Cnt := EbN(First);
    O.VerticesCount := Cnt;
    For k := 0 To Cnt - 1 Do
        O.Vertex[k + 1] := Point(EbSchC(First + 1 + 2 * k), EbSchC(First + 2 + 2 * k));
End;

Procedure MakePin(Comp : ISch_Component);
Var
    P : ISch_Pin;
Begin
    P := SchServer.SchObjectFactory(ePin, eCreate_Default);
    P.Designator := EbF[3];
    P.Name := EbF[4];
    P.Electrical := EbN(5);
    P.Orientation := EbN(6);
    P.Location := Point(EbSchC(7), EbSchC(8));
    P.PinLength := EbSchC(9);
    P.ShowName := EbN(10) = 1;
    P.ShowDesignator := EbN(11) = 1;
    P.IsHidden := EbN(12) = 1;
    P.Symbol_InnerEdge := EbN(13);
    P.Symbol_OuterEdge := EbN(14);
    P.Symbol_Inner := EbN(15);
    P.Symbol_Outer := EbN(16);
    P.Description := EbF[17];
    If EbF[18] <> '' Then
        P.HiddenNetName := EbF[18];
    Own(Comp, P);
End;

Procedure MakeRect(Comp : ISch_Component);
Var
    R : ISch_Rectangle;
Begin
    R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
    R.Location := Point(EbSchC(3), EbSchC(4));
    R.Corner := Point(EbSchC(5), EbSchC(6));
    R.LineWidth := EbN(7);
    Filled(R, EbF[8]);
    Own(Comp, R);
End;

Procedure MakeLine(Comp : ISch_Component);
Var
    L : ISch_Line;
Begin
    L := SchServer.SchObjectFactory(eLine, eCreate_Default);
    L.Location := Point(EbSchC(3), EbSchC(4));
    L.Corner := Point(EbSchC(5), EbSchC(6));
    L.LineWidth := EbN(7);
    L.Color := 128;
    Own(Comp, L);
End;

Procedure MakePoly(Comp : ISch_Component; Closed : Boolean);
Var
    P : ISch_Polyline;
Begin
    If Closed Then
    Begin
        P := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
        P.LineWidth := EbN(3);
        Filled(P, EbF[4]);
        Vertices(P, 5);
    End
    Else
    Begin
        P := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
        P.LineWidth := EbN(3);
        P.Color := 128;
        Vertices(P, 4);
    End;
    Own(Comp, P);
End;

Procedure MakeSchArc(Comp : ISch_Component);
Var
    A : ISch_Arc;
Begin
    A := SchServer.SchObjectFactory(eArc, eCreate_Default);
    A.Location := Point(EbSchC(3), EbSchC(4));
    A.Radius := EbSchC(5);
    A.StartAngle := EbAng(6);
    A.EndAngle := EbAng(7);
    A.LineWidth := EbN(8);
    A.Color := 128;
    Own(Comp, A);
End;

Procedure MakeCircle(Comp : ISch_Component);
Var
    E : ISch_Ellipse;
Begin
    E := SchServer.SchObjectFactory(eEllipse, eCreate_Default);
    E.Location := Point(EbSchC(3), EbSchC(4));
    E.Radius := EbSchC(5);
    E.SecondaryRadius := EbSchC(5);
    E.LineWidth := EbN(6);
    Filled(E, EbF[7]);
    Own(Comp, E);
End;

Procedure MakeLabel(Comp : ISch_Component);
Var
    L : ISch_Label;
Begin
    L := SchServer.SchObjectFactory(eLabel, eCreate_Default);
    L.Location := Point(EbSchC(3), EbSchC(4));
    L.Orientation := EbN(5);
    L.Text := EbF[6];
    L.Color := 128;
    Own(Comp, L);
End;

Procedure MakeParam(Comp : ISch_Component);
Var
    P : ISch_Parameter;
Begin
    P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
    P.Name := EbF[1];
    P.Text := EbF[2];
    P.IsHidden := True;
    Comp.AddSchObject(P);
End;

Procedure LinkFootprint(Comp : ISch_Component);
Var
    Impl : ISch_Implementation;
Begin
    Impl := Comp.AddSchImplementation;
    Impl.ClearAllDatafileLinks;
    Impl.ModelName := EbF[1];
    Impl.ModelType := 'PCBLIB';
    Impl.AddDataFileLink(EbF[1], '', 'PCBLIB');
    Impl.IsCurrent := True;
End;

Function BuildSchLib(Folder : String) : String;
Var
    Path  : String;
    Doc   : IServerDocument;
    Lib   : ISch_Lib;
    Blank : ISch_Component;
    Comp  : ISch_Component;
    Kind  : String;
    Count : Integer;
Begin
    Path := Folder + EbF[1];
    CloseIfOpen(Path);
    EbErr := EbSetAside(Path);
    If EbErr <> '' Then
    Begin
        SkipBlock;
        Result := 'FAILED' + #9 + 'SCHLIB' + #9 + Path + #9 + EbErr;
        Exit;
    End;
    Doc := Client.OpenNewDocument('SCHLIB', Path, '', False);
    Client.ShowDocument(Doc);
    Lib := SchServer.GetCurrentSchDocument;
    Blank := Lib.CurrentSchComponent;
    Comp := Nil;
    Count := 0;
    Inc(EbIdx);
    While EbIdx < EbLines.Count Do
    Begin
        EbSplit(EbLines[EbIdx]);
        Kind := EbF[0];
        If Kind = 'END' Then Break;
        If Kind = 'SYMBOL' Then
        Begin
            If Comp <> Nil Then
                SchServer.RobotManager.SendMessage(Lib.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Comp.I_ObjectAddress);
            Comp := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
            Comp.CurrentPartID := 1;
            Comp.DisplayMode := 0;
            Comp.LibReference := EbF[1];
            Comp.ComponentDescription := EbF[2];
            Comp.Designator.Text := EbF[3];
            Comp.Comment.Text := EbF[4];
            Comp.PartCount := EbN(5);          { always: a new component has 0 parts (saved as PARTCOUNT=1, Altium wants 2) }
            If EbN(6) > 1 Then
                Comp.DisplayModeCount := EbN(6);
            Lib.AddSchComponent(Comp);
            Inc(Count);
        End
        Else If Kind = 'PIN' Then MakePin(Comp)
        Else If Kind = 'RECT' Then MakeRect(Comp)
        Else If Kind = 'LINE' Then MakeLine(Comp)
        Else If Kind = 'POLYLINE' Then MakePoly(Comp, False)
        Else If Kind = 'POLYGON' Then MakePoly(Comp, True)
        Else If Kind = 'SARC' Then MakeSchArc(Comp)
        Else If Kind = 'CIRCLE' Then MakeCircle(Comp)
        Else If Kind = 'LABEL' Then MakeLabel(Comp)
        Else If Kind = 'PARAM' Then MakeParam(Comp)
        Else If Kind = 'DESIGNATOR' Then Comp.Designator.Location := Point(EbSchC(1), EbSchC(2))
        Else If Kind = 'COMMENT' Then Comp.Comment.Location := Point(EbSchC(1), EbSchC(2))
        Else If Kind = 'FOOTPRINTLINK' Then LinkFootprint(Comp);
        Inc(EbIdx);
    End;
    If Comp <> Nil Then
    Begin
        SchServer.RobotManager.SendMessage(Lib.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Comp.I_ObjectAddress);
        Lib.CurrentSchComponent := Comp;
    End;
    If (Blank <> Nil) And (Count > 0) Then
        Lib.RemoveSchComponent(Blank);
    Lib.GraphicallyInvalidate;
    Doc.SetModified(True);
    Doc.DoFileSave('SCHLIB');
    Client.CloseDocument(Doc);
    Result := EbFinish(Path, 'SCHLIB', IntToStr(Count) + ' symbol(s)');
End;

{ ------------------------------------------------------------------ entry points }
Procedure BuildLibraries(Folder : String);
Var
    Kind, Name, Line : String;
Begin
    EbF := TStringList.Create;
    EbLines := TStringList.Create;
    EbReport := TStringList.Create;
    Try
        If Not FileExists(Folder + 'ecadlib_build.txt') Then
        Begin
            EbReport.Add('FAILED' + #9 + 'ecadlib_build.txt' + #9 + Folder + #9 + 'no build file here');
            Exit;
        End;
        EbLines.LoadFromFile(Folder + 'ecadlib_build.txt');
        EbIdx := 0;
        While EbIdx < EbLines.Count Do
        Begin
            EbSplit(EbLines[EbIdx]);
            Kind := EbF[0];
            If (Kind = 'PCBLIB') Or (Kind = 'SCHLIB') Then
            Begin
                Name := EbF[1];
                EbErr := '';
                Try
                    If Kind = 'PCBLIB' Then
                        Line := BuildPcbLib(Folder)
                    Else
                        Line := BuildSchLib(Folder);
                    EbReport.Add(Line);
                Except
                    EbReport.Add('FAILED' + #9 + Kind + #9 + Folder + Name + #9 + 'Altium raised an error while building it' + EbPutBack(Folder + Name));
                    SkipBlock;
                End;
            End;
            Inc(EbIdx);
        End;
    Finally
        EbReport.SaveToFile(Folder + 'ecadlib_build_report.txt');
        EbReport.Free;
        EbLines.Free;
        EbF.Free;
    End;
End;

Procedure BuildLibrariesInAltium;
Begin
    BuildLibraries(ExtractFilePath(GetRunningScriptProjectName));
    ShowMessage('ecadlib: libraries built by Altium. See ecadlib_build_report.txt next to this script.');
End;
