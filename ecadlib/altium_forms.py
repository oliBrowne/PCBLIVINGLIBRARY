"""The ecadlib dialog inside Altium Designer: a DelphiScript form (.pas + .dfm) the menu's entries open.

It looks like Altium's own dark dialogs and does the work in place, as the landing page's workflow shows it:

    ecadlib . Check project BOM      a big count ("24 lines"), one row per kind (already in your library, a source can
                                     supply, needs a footprint), a progress line, then Later / Fill the library now
    ecadlib . Add part from MPN      MPN + manufacturer, then the lookup live (where ecadlib looked, what it took, the
                                     release check), then Done; the Components panel shows the part after the reinstall
    ecadlib . Fill the library       the librarian's progress part by part, then ready / held counts

The dialog never waits in a loop: a TTimer (every 250 ms) reads <job>/progress.ini while ecadlib runs (and redraws
only what changed) and <job>/result.ini when exit.txt appears, so Altium keeps painting. Only a step that needs a browser opens one: the
report of a BOM check (on request) and the review queue for a held part. Everything the dialog needs from the menu
unit (StartJob, Ask, Answer, CommandLine, Reinstall, Setting...) is called across units, as Altium script projects
allow (Altium-Designer-addons OpenLib calls its libUtils unit the same way).

Event handlers are named in the .dfm (OnClick = PrimaryClick): DelphiScript cannot assign one to a control in code
(Altium-Designer-addons ReturnViaCheck.pas: "CheckBox.OnClick := ... throws error")."""
from . import __version__, altium_native as AN

DIALOG_UNIT, DIALOG_FORM = "ecadlib_dialog.pas", "ecadlib_dialog.dfm"
ROWS = 7
WIDTH = 420
LABEL_W = 190             # the rows' left column; the value column gets the rest (Consolas: about 7 px a character)

# Altium's dark UI, as Delphi TColor values ($00BBGGRR)
BG, HEAD, TEXT, DIM, RULE = "$002E2A2A", "$00242121", "$00E8E8E8", "$009C9C9C", "$00433D3D"
BLUE, BLUE_OFF, GREEN, RED, BTN, FIELD = "$00D9772F", "$00705A4A", "$005AB85C", "$006060E0", "$00443E3E", "$00201D1D"
# the buttons under the mouse (HOT) and held down (DOWN), like Altium's own flat buttons; the X turns red when hot
BLUE_HOT, BLUE_DOWN, BTN_HOT, BTN_DOWN, CLOSE_HOT = "$00E8893F", "$00B8621F", "$00564F4F", "$00363131", "$003A3AC4"
LOGO = 18                 # the ecadlib mark at the left of the header, in pixels (small: Altium's own icons are 16)

# Every identifier the dialog's .pas uses besides its own routines, the menu unit's and the .dfm's component names:
# VCL properties, methods and constants of the script form components (Altium "Forms & Components":
# altium.com/documentation/altium-designer/scripting/delphiscript/forms-components; TR0120 p10-11).
FORM_API = {
    "Caption": "TLabel / TPanel / TForm caption", "Visible": "TControl.Visible", "Top": "TControl.Top",
    "Left": "TControl.Left", "Width": "TControl.Width", "Height": "TControl.Height", "ClientHeight": "TForm.ClientHeight",
    "Font": "TControl.Font", "Color": "TControl.Color / TFont.Color", "Text": "TEdit.Text", "Enabled": "TTimer.Enabled",
    "Interval": "TTimer.Interval", "ModalResult": "TForm.ModalResult (Forms & Components: ShowModal example)",
    "ShowModal": "TForm.ShowModal (Forms & Components)", "mrOk": "TModalResult", "mrCancel": "TModalResult",
    "caNone": "TCloseAction (OnClose keeps the form open)", "CloseAction": "OnClose Var parameter",
    "Cursor": "TControl.Cursor", "crHandPoint": "TCursor", "crDefault": "TCursor",
    "SetFocus": "TWinControl.SetFocus", "Update": "TControl.Update (paints now, before a long Altium step)", "SelectAll": "TEdit.SelectAll",
    "EcadlibDialog": "the form instance a script form unit gets (named after the .dfm's object)",
    "Sender": "event handler parameter", "TObject": "event handler parameter type", "Style": "TFont.Style",
    "ActiveControl": "TForm.ActiveControl", "AutoSize": "TLabel.AutoSize", "TEdit": "Forms & Components: TEdit",
    "PasteFromClipboard": "TCustomEdit.PasteFromClipboard", "CopyToClipboard": "TCustomEdit.CopyToClipboard",
    "CutToClipboard": "TCustomEdit.CutToClipboard", "Word": "OnKeyDown Key type", "TShiftState": "OnKeyDown Shift type",
    "Key": "OnKeyDown parameter", "TMouseButton": "OnMouseDown / OnMouseUp Button type", "InSet": "DelphiScript set test (InSet(ssCtrl, Shift))", "ssCtrl": "TShiftState", "ssShift": "TShiftState",
}


def _dlg_pas():
    """The dialog unit: altium_scripts/ecadlib_dialog.pas (LF). Its Row1..Row7 and colours match ROWS and the
    colour constants above, which the .dfm uses (tests/test_altium_forms.py checks they agree)."""
    return AN.script_text(DIALOG_UNIT).replace("\r\n", "\n")


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


DRAG = ("Cursor = crSizeAll", "OnMouseDown = HeadMouseDown", "OnMouseMove = HeadMouseMove", "OnMouseUp = HeadMouseUp")


def _drag(ind):
    return "".join(f"{ind}{x}\n" for x in DRAG)


def _label(name, left, top, width, height, caption, ind="    ", align="", wrap=False, drag=False, **font):
    i2 = ind + "  "
    extra = (f"{i2}Alignment = taRightJustify\n" if align == "r" else "") + (f"{i2}WordWrap = True\n" if wrap else "") \
        + (_drag(i2) if drag else "")
    return (f"{ind}object {name}: TLabel\n{i2}Left = {left}\n{i2}Top = {top}\n{i2}Width = {width}\n{i2}Height = {height}\n"
            f"{i2}AutoSize = False\n{i2}Caption = {_dfm_str(caption)}\n{_font(ind=i2, **font)}"
            f"{i2}Transparent = True\n{extra}{ind}end\n")


def _hover(prefix, ind, press=True):
    """A button's own highlight: lighter under the mouse, darker while held (handlers <prefix>Enter / Leave / Down /
    Up in the unit). TPanel has no hot or pressed look of its own, so without these it looks dead under the mouse."""
    ev = ("Enter", "Leave") + (("Down", "Up") if press else ())
    return "".join(f"{ind}OnMouse{e} = {prefix}{e}\n" for e in ev)


def _panel(name, left, top, width, height, color, body="", caption="", ind="  ", onclick="", font=None, visible=True,
           drag=False, hover="", press=True):
    i2 = ind + "  "
    return (f"{ind}object {name}: TPanel\n{i2}Left = {left}\n{i2}Top = {top}\n{i2}Width = {width}\n{i2}Height = {height}\n"
            f"{i2}BevelOuter = bvNone\n{i2}Caption = {_dfm_str(caption)}\n{i2}Color = {color}\n{i2}ParentBackground = False\n"
            + (_font(ind=i2, **font) if font else "") + (f"{i2}Cursor = crHandPoint\n{i2}OnClick = {onclick}\n" if onclick else "")
            + ("" if visible else f"{i2}Visible = False\n") + (_drag(i2) if drag else "")
            + (_hover(hover, i2, press) if hover else "") + body + f"{ind}end\n")


def _edit(name, left, top, width, ind="      "):
    i2 = ind + "  "
    return (f"{ind}object {name}: TEdit\n{i2}Left = {left}\n{i2}Top = {top}\n{i2}Width = {width}\n{i2}Height = 24\n"
            f"{i2}BorderStyle = bsSingle\n{i2}Color = {FIELD}\n{_font(ind=i2)}{i2}Text = ''\n{ind}end\n")


def _paste_link(w, ind="      "):
    """'Paste' at the right of the MPN label: pastes the clipboard into the MPN box (and so does Ctrl+V)."""
    i2 = ind + "  "
    return (f"{ind}object PasteMpn: TLabel\n{i2}Left = {w - 60}\n{i2}Top = 0\n{i2}Width = 60\n{i2}Height = 16\n"
            f"{i2}Alignment = taRightJustify\n{i2}AutoSize = False\n{i2}Caption = 'Paste'\n{i2}Cursor = crHandPoint\n"
            f"{_font(color=BLUE, ind=i2)}{i2}Transparent = True\n{i2}OnClick = PasteMpnClick\n{ind}end\n")


def _logo_bmp(size=LOGO):
    """The ecadlib mark (brand.mark_rgba, on its dark tile) laid over the header colour as a 24-bit BMP file."""
    import struct
    from . import brand
    hb = int(HEAD[1:], 16)
    ground = (hb & 255, (hb >> 8) & 255, (hb >> 16) & 255)          # $00BBGGRR -> R, G, B
    stride = (size * 3 + 3) & ~3
    pix = bytearray()
    for row in reversed(brand.mark_rgba(size, tile=True)):         # BMP rows go bottom up
        line = bytearray()
        for x in range(size):
            r, g, b, a = row[4 * x:4 * x + 4]
            mix = [round((c * a + k * (255 - a)) / 255) for c, k in zip((r, g, b), ground)]
            line += bytes((mix[2], mix[1], mix[0]))
        pix += line + bytes(stride - len(line))
    head = struct.pack("<2sIHHI", b"BM", 54 + len(pix), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, size, size, 1, 24, 0, len(pix), 2835, 2835, 0, 0)
    return head + info + bytes(pix)


def _logo(ind="    "):
    """A TImage holding the mark: Picture.Data is the class name (TBitmap) as a short string, the bitmap's size as a
    4-byte number, then the .bmp file, in hex (what Delphi itself writes for a TImage picture in a text .dfm)."""
    bmp = _logo_bmp()
    data = bytes((7,)) + b"TBitmap" + len(bmp).to_bytes(4, "little") + bmp
    hexs = data.hex().upper()
    i2, i3 = ind + "  ", ind + "    "
    lines = "\n".join(i3 + hexs[k:k + 64] for k in range(0, len(hexs), 64))
    return (f"{ind}object Logo: TImage\n{i2}Left = 12\n{i2}Top = {(34 - LOGO) // 2}\n{i2}Width = {LOGO}\n"
            f"{i2}Height = {LOGO}\n{i2}Picture.Data = {{\n{lines}}}\n{i2}Transparent = False\n{_drag(i2)}{ind}end\n")


def dialog_dfm_text():
    """The form: header with the logo, title and close mark, the MPN box, the big line, ROWS rows, status, meter, note, report
    link and two buttons. Positions are set again by Layout at run time; these are the first frame's."""
    w = WIDTH - 32
    rows = "".join(_panel(f"Row{i}", 16, 90 + 26 * i, w, 26, BG, visible=False, body=(
        _label(f"Row{i}A", 0, 4, LABEL_W, 18, "", face="Consolas") +
        _label(f"Row{i}B", LABEL_W, 4, w - LABEL_W, 18, "", align="r", face="Consolas") +
        _panel(f"Row{i}Rule", 0, 25, w, 1, RULE, ind="    "))) for i in range(1, ROWS + 1))
    ask = _panel("AskBox", 16, 46, w, 104, BG, visible=False, body=(
        _label("AskMpn", 0, 0, w, 16, "MPN", color=DIM) + _paste_link(w) + _edit("EditMpn", 0, 18, w) +
        _label("AskMaker", 0, 52, w, 16, "Manufacturer (optional)", color=DIM) + _edit("EditMaker", 0, 70, w)))
    meter = _panel("MeterTrack", 16, 300, w, 2, RULE, visible=False,
                   body=_panel("MeterFill", 0, 0, 0, 2, BLUE, ind="    "))
    button_font = {"color": "$00FFFFFF"}
    head = _panel("Head", 0, 0, WIDTH, 34, HEAD, drag=True, body=(
        _label("HeadTitle", 12 + LOGO + 8, 8, WIDTH - 60 - LOGO - 8, 18, "ecadlib · Check project BOM", drag=True) +
        _panel("HeadClose", WIDTH - 34, 2, 30, 30, HEAD, caption="×", ind="    ", onclick="CloseClick",
               font={"color": DIM, "size": -16}, hover="HeadClose", press=False) + _logo()))
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
  OnKeyDown = EcadlibDialogKeyDown
  OnKeyPress = EcadlibDialogKeyPress
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
{_panel("ReleaseBtn", 16, 350, 128, 30, BTN, caption="Release anyway", onclick="ReleaseClick", font=button_font, visible=False, hover="Release")}{_panel("Secondary", 120, 350, 80, 30, BTN, caption="Later", onclick="SecondaryClick", font=button_font, hover="Secondary")}{_panel("Primary", 208, 350, 136, 30, BLUE, caption="OK", onclick="PrimaryClick", font=button_font, hover="Primary")}  object EnterKey: TButton
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
