#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arrange Footprints in Circle — KiCad Action Plugin

Moves the SELECTED footprints onto a circle (center = centroid of the
selection). Nothing is duplicated -> every footprint keeps its schematic
link (no "Update PCB from Schematic" conflict). Ideal e.g. for rings of
serially-chained RGB LEDs.

The dialog shows a live preview (rendered into a wx.StaticBitmap via
MemoryDC): each footprint is drawn as its pad bounding box, rotated
according to the current settings. Tick/untick "Rotate parts" to see
what the rotation option does before applying.

Options:
  - Radius [mm]      (default: mean distance of selection from centroid;
                      ~0 for freshly imported parts -> type your own)
  - Start angle      (0 = right/east, -90 = top/12 o'clock, default -90)
  - Clockwise        (default on)
  - Rotate parts     rotate each footprint so its 0 deg direction points
                      radially outward (default on)
  - Keep silkscreen upright (opt-in): rotate parts but counter-rotate
                      text on silkscreen layers so it stays readable
                      (0/180 deg rule, like KiCad's "keep upright")
Placement order: ascending by reference number (D1, D2, ... D10).

Normal edit: undoable with Ctrl+Z.
"""

import re
import math

import pcbnew
import wx


def _ref_sort_key(fp):
    """Natural reference sort key (D2 before D10)."""
    ref = fp.GetReference()
    m = re.search(r"(\d+)", ref)
    num = int(m.group(1)) if m else 0
    prefix = re.sub(r"\d+", "", ref)
    return (prefix, num)


def _pad_bbox_mm(fp):
    """Union of all pad bounding boxes -> (width_mm, height_mm).

    Used by the preview so each part is drawn at roughly its real size.
    Falls back to the footprint bounding box if a pad query fails.
    """
    try:
        pads = list(fp.Pads())
    except Exception:
        pads = []
    if pads:
        try:
            min_x = min(p.GetBoundingBox().GetLeft() for p in pads)
            max_x = max(p.GetBoundingBox().GetRight() for p in pads)
            min_y = min(p.GetBoundingBox().GetTop() for p in pads)
            max_y = max(p.GetBoundingBox().GetBottom() for p in pads)
            return ((max_x - min_x) / 1e6, (max_y - min_y) / 1e6)
        except Exception:
            pass
    try:
        bb = fp.GetBoundingBox()
        return (bb.GetWidth() / 1e6, bb.GetHeight() / 1e6)
    except Exception:
        return (1.0, 1.0)


def _original_rotation_deg(fp):
    """Current absolute rotation of the footprint in degrees."""
    try:
        return float(fp.GetOrientationDegrees())
    except Exception:
        return 0.0


PREVIEW_PX = 320


def _render_ring(parts, radius_mm, start_deg, clockwise, rotate_parts,
                 keep_silk_upright=False):
    """Render ring preview into a wx.Bitmap (MemoryDC based, reliable).

    parts: list of dicts {ref, w_mm, h_mm}
    Angle convention matches the placement math: 0 deg = east,
    -90 deg = north/12 o'clock, y grows downwards like in the PCB editor.

    Labels follow the parts: when "rotate parts" is on they rotate with
    the box (unless "keep silkscreen upright" counter-rotates them to
    the 0/180 deg rule) — matching what happens to real silk text.
    """
    bmp = wx.Bitmap(PREVIEW_PX, PREVIEW_PX)
    mdc = wx.MemoryDC()
    mdc.SelectObject(bmp)

    mdc.SetBackground(wx.Brush(wx.Colour(255, 255, 255)))
    mdc.Clear()

    w = h = PREVIEW_PX
    cx, cy = w / 2.0, h / 2.0
    n = len(parts)

    eff_radius = radius_mm if radius_mm > 0 else 10.0
    max_r = min(w, h) / 2.0 - 60
    scale = max_r / eff_radius

    def to_px(r_mm, deg):
        rad = math.radians(deg)
        x = cx + r_mm * scale * math.cos(rad)
        y = cy + r_mm * scale * math.sin(rad)
        return x, y

    def draw_rotated_box(cx_, cy_, w_mm, h_mm, deg, pen, brush):
        """Draw a rectangle centered at (cx_,cy_), rotated by deg."""
        rad = math.radians(deg)
        c, s = math.cos(rad), math.sin(rad)
        hw, hh = (w_mm * scale) / 2.0, (h_mm * scale) / 2.0
        pts = []
        for lx, ly in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
            px = cx_ + lx * c - ly * s
            py = cy_ + lx * s + ly * c
            pts.append((int(px), int(py)))
        mdc.SetPen(pen)
        mdc.SetBrush(brush)
        mdc.DrawPolygon(pts)

    # Compass / angle axes
    small_font = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                         wx.FONTWEIGHT_NORMAL)
    mdc.SetFont(small_font)
    mdc.SetPen(wx.Pen(wx.Colour(215, 215, 215), 1, wx.PENSTYLE_DOT))
    for label, deg in (("0", 0), ("90", 90), ("180", 180), ("-90", -90)):
        x, y = to_px(eff_radius, deg)
        mdc.DrawLine(int(cx), int(cy), int(x), int(y))
        lx, ly = to_px(eff_radius + 4, deg)
        mdc.SetTextForeground(wx.Colour(150, 150, 150))
        mdc.DrawText(label, int(lx) - 10, int(ly) - 8)

    # Ring outline
    mdc.SetPen(wx.Pen(wx.Colour(190, 190, 190), 1))
    mdc.SetBrush(wx.TRANSPARENT_BRUSH)
    mdc.DrawCircle(int(cx), int(cy), int(eff_radius * scale))

    if n < 1:
        mdc.SelectObject(wx.NullBitmap)
        return bmp

    step = 360.0 / n
    font = wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                   wx.FONTWEIGHT_NORMAL)
    mdc.SetFont(font)

    for i, part in enumerate(parts):
        if clockwise:
            deg = start_deg + i * step
        else:
            deg = start_deg - i * step

        # Normalize box so the smallest ring parts stay visible
        w_mm = max(part["w_mm"], 0.8)
        h_mm = max(part["h_mm"], 0.8)

        if i == 0:
            pen = wx.Pen(wx.Colour(200, 30, 30), 2)
            brush = wx.Brush(wx.Colour(255, 230, 230))
        else:
            pen = wx.Pen(wx.Colour(30, 90, 200), 2)
            brush = wx.Brush(wx.Colour(225, 238, 255))

        box_rot = deg if rotate_parts else 0.0
        x, y = to_px(eff_radius, deg)
        draw_rotated_box(x, y, w_mm, h_mm, box_rot, pen, brush)

        # Label
        lx, ly = to_px(eff_radius, deg)
        if lx > cx:
            tx = int(lx) + int(w_mm * scale) + 6
        else:
            tx = int(lx) - int(w_mm * scale) - 6
        # Label — rotates like real silk text: with the part unless
        # "keep silkscreen upright" holds it at 0/180 deg.
        label_rot = 0.0
        if rotate_parts:
            if keep_silk_upright:
                a = deg % 360.0
                if a > 180.0:
                    a -= 360.0
                label_rot = 0.0 if -90.0 <= a <= 90.0 else 180.0
            else:
                label_rot = deg
        mdc.SetTextForeground(wx.Colour(20, 20, 20))
        if label_rot == 0.0:
            if tx < 2:
                tx = 2
            elif tx > PREVIEW_PX - 40:
                tx = PREVIEW_PX - 40
            mdc.DrawText(part["ref"], tx, int(ly) - 7)
        else:
            # DrawRotatedText takes degrees, positive = CCW. Anchor (tx,
            # ly-7) behaves like DrawText's top-left at 0 deg.
            mdc.DrawRotatedText(part["ref"], tx, int(ly) - 7, label_rot)

    # Start marker
    sx, sy = to_px(eff_radius, start_deg)
    mdc.SetTextForeground(wx.Colour(200, 30, 30))
    mdc.DrawText("start", int(sx) + 8, int(sy) + 10)

    mdc.SelectObject(wx.NullBitmap)
    return bmp


class CircleDialog(wx.Dialog):
    def __init__(self, parent, default_radius, parts):
        super().__init__(parent, title="Arrange Footprints in Circle",
                         size=(720, 560))
        self._parts = list(parts)

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        top = wx.BoxSizer(wx.HORIZONTAL)

        # --- Left: parameters ---
        grid = wx.GridBagSizer(6, 8)

        def add_row(row, label, ctrl):
            grid.Add(wx.StaticText(panel, label=label), (row, 0),
                     flag=wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, (row, 1), flag=wx.EXPAND)

        self.radius = wx.TextCtrl(panel, value=f"{default_radius:.2f}")
        add_row(0, "Radius [mm]:", self.radius)

        self.start = wx.TextCtrl(panel, value="-90")
        add_row(1, "Start angle [deg]:", self.start)

        self.cw = wx.CheckBox(panel, label="clockwise")
        self.cw.SetValue(True)
        grid.Add(self.cw, (2, 0), span=(1, 2))

        self.rotate = wx.CheckBox(panel,
                                  label="Rotate parts (radially outward)")
        self.rotate.SetValue(True)
        grid.Add(self.rotate, (3, 0), span=(1, 2))

        self.silk = wx.CheckBox(
            panel, label="Keep silkscreen text upright")
        self.silk.SetValue(False)
        grid.Add(self.silk, (4, 0), span=(1, 2))

        refs = ", ".join(p["ref"] for p in parts)
        grid.Add(wx.StaticText(panel,
                               label=f"{len(parts)} footprints selected: "
                                     f"{refs}"),
                 (5, 0), span=(1, 2), flag=wx.TOP)

        hint = ("Preview: red box = start (first part).\n"
                "0 = east, -90 = north (12 o'clock).\n"
                "Order = reference ascending.\n"
                "Tick/untick \"Rotate parts\" to see the effect.")
        grid.Add(wx.StaticText(panel, label=hint), (6, 0), span=(1, 2),
                 flag=wx.TOP)

        grid.AddGrowableCol(1)
        top.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        # --- Right: preview ---
        self.preview = wx.StaticBitmap(panel, size=(PREVIEW_PX, PREVIEW_PX))
        self.preview.SetMinSize((PREVIEW_PX, PREVIEW_PX))
        top.Add(self.preview, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        # --- Buttons ---
        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(panel, wx.ID_OK, "OK")
        cancel = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        btns.AddButton(ok)
        btns.AddButton(cancel)
        btns.Realize()

        sizer.Add(top, 1, wx.ALL | wx.EXPAND, 10)
        sizer.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.Fit()
        self.Centre()

        # Live update
        for ctrl in (self.radius, self.start):
            ctrl.Bind(wx.EVT_TEXT, self._on_change)
        for cb in (self.cw, self.rotate, self.silk):
            cb.Bind(wx.EVT_CHECKBOX, self._on_change)

        self._on_change()
        self.Layout()

    def _on_change(self, _evt=None):
        try:
            radius = float(self.radius.GetValue())
            start = float(self.start.GetValue())
        except ValueError:
            radius = 0.0
            start = -90.0
        bmp = _render_ring(self._parts, radius, start,
                           self.cw.IsChecked(), self.rotate.IsChecked(),
                           self.silk.IsChecked())
        self.preview.SetBitmap(bmp)
        self.preview.Refresh()

    def get_values(self):
        try:
            radius_mm = float(self.radius.GetValue())
            start_deg = float(self.start.GetValue())
        except ValueError:
            return None
        return {
            "radius_mm": radius_mm,
            "start_deg": start_deg,
            "clockwise": self.cw.IsChecked(),
            "rotate_parts": self.rotate.IsChecked(),
            "keep_silk_upright": self.silk.IsChecked(),
        }


def _keep_text_upright(fp, footprint_angle_deg):
    """Counter-rotate silkscreen text so it stays readable.

    KiCad stores child angles (texts, pads) in .kicad_pcb as ABSOLUTE
    board angles (footprint rotation + local rotation). So to make a text
    read upright on the board, set its stored angle directly to 0 or 180
    (KiCad's "keep upright" 0/180 rule), NOT relative to the footprint.
    """
    a = footprint_angle_deg % 360.0
    if a > 180.0:
        a -= 360.0
    upright_abs = 0.0 if -90.0 <= a <= 90.0 else 180.0

    silk_layers = (pcbnew.F_SilkS, pcbnew.B_SilkS)
    for text in (fp.Reference(), fp.Value()):
        try:
            if text.GetLayer() in silk_layers:
                text.SetTextAngleDegrees(upright_abs)
        except Exception:
            pass


class ArrangeInCirclePlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Arrange Footprints in Circle"
        self.category = "Modify PCB"
        self.description = ("Move selected footprints evenly onto a circle "
                            "(centroid = center). No duplication, schematic "
                            "links stay intact. Live preview included.")
        self.show_toolbar_button = False

    def Run(self):
        board = pcbnew.GetBoard()
        fps = [f for f in board.GetFootprints() if f.IsSelected()]

        if len(fps) < 2:
            wx.MessageBox(
                "Please select at least 2 footprints.",
                "Arrange in Circle", wx.OK | wx.ICON_INFORMATION)
            return

        fps_sorted = sorted(fps, key=_ref_sort_key)
        parts = []
        for fp in fps_sorted:
            w_mm, h_mm = _pad_bbox_mm(fp)
            parts.append({"ref": fp.GetReference(),
                          "w_mm": w_mm, "h_mm": h_mm})

        # Centroid of the selection
        cx = sum(f.GetPosition().x for f in fps) / len(fps)
        cy = sum(f.GetPosition().y for f in fps) / len(fps)
        center = pcbnew.VECTOR2I(int(cx), int(cy))

        avg_r = sum(math.hypot(f.GetPosition().x - cx, f.GetPosition().y - cy)
                    for f in fps) / len(fps)
        avg_r_mm = avg_r / 1e6

        dlg = CircleDialog(None, avg_r_mm, parts)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        vals = dlg.get_values()
        dlg.Destroy()
        if vals is None:
            wx.MessageBox("Invalid numeric input.", "Arrange in Circle",
                          wx.OK | wx.ICON_ERROR)
            return

        radius_nm = int(vals["radius_mm"] * 1e6)
        n = len(fps_sorted)
        step = 360.0 / n

        for i, fp in enumerate(fps_sorted):
            if vals["clockwise"]:
                angle_deg = vals["start_deg"] + i * step
            else:
                angle_deg = vals["start_deg"] - i * step

            rad = math.radians(angle_deg)
            pos = pcbnew.VECTOR2I(
                int(center.x + radius_nm * math.cos(rad)),
                int(center.y + radius_nm * math.sin(rad)))

            if vals["rotate_parts"]:
                fp.SetOrientationDegrees(angle_deg)
                if vals["keep_silk_upright"]:
                    _keep_text_upright(fp, angle_deg)

            fp.SetPosition(pos)

        pcbnew.Refresh()


ArrangeInCirclePlugin().register()
