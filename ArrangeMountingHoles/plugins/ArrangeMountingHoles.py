#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arrange Mounting Holes — KiCad Action Plugin

Select mounting-hole footprints (4, 6, ...) and arrange them into a
clean grid (columns x rows) with exact X/Y spacing (e.g. 40 x 40 mm).
Nothing is duplicated, nothing is aligned to edge cuts — the holes are
simply snapped to the grid positions of a centered rectangle, each hole
moving to its nearest free grid point.

The dialog shows a live preview: grey dots are the holes' current
positions, coloured boxes are the target grid points (each labelled with
the reference that will land there). Optional: group the footprints so
they stay together as one movable unit.

Normal edit: undoable with Ctrl+Z.
"""

import math

import pcbnew
import wx


def _ref_sort_key(fp):
    """Natural reference sort key (H2 before H10)."""
    import re
    ref = fp.GetReference()
    m = re.search(r"(\d+)", ref)
    num = int(m.group(1)) if m else 0
    prefix = re.sub(r"\d+", "", ref)
    return (prefix, num)


def _already_grouped(fps):
    """True if all footprints share the same non-nil parent group."""
    ids = set()
    for fp in fps:
        try:
            gid = fp.GetParentGroupId().AsString()
        except Exception:
            return False
        ids.add(gid)
    return len(ids) == 1 and "00000000-0000-0000-0000-000000000000" not in ids


def _default_grid(n):
    """(cols, rows) for n items — factors closest to a square."""
    best = (1, n)
    for c in range(1, int(math.sqrt(n)) + 1):
        if n % c == 0:
            best = (c, n // c)
    return best


PREVIEW_PX = 320


def _grid_positions(cols, rows, x_mm, y_mm):
    """Target grid offsets (mm) around the origin, row-major from top-left."""
    out = []
    for r in range(rows):
        for c in range(cols):
            ox = (c - (cols - 1) / 2.0) * x_mm
            oy = (r - (rows - 1) / 2.0) * y_mm
            out.append((ox, oy))
    return out


def _render_preview(parts, cols, rows, x_mm, y_mm):
    """Render grid preview into a wx.Bitmap.

    parts: list of dicts {ref, x_mm, y_mm} = current positions relative
    to the centroid (mm). cols/rows/x_mm/y_mm = requested grid.
    Grey dots = current holes, coloured boxes = target grid points
    labelled with the reference that will land there.
    """
    bmp = wx.Bitmap(PREVIEW_PX, PREVIEW_PX)
    mdc = wx.MemoryDC()
    mdc.SelectObject(bmp)
    mdc.SetBackground(wx.Brush(wx.Colour(255, 255, 255)))
    mdc.Clear()

    w = h = PREVIEW_PX
    cx, cy = w / 2.0, h / 2.0
    n = len(parts)
    if n < 1 or cols < 1 or rows < 1:
        mdc.SelectObject(wx.NullBitmap)
        return bmp

    cols = max(1, int(cols))
    rows = max(1, int(rows))
    targets = _grid_positions(cols, rows,
                              x_mm if x_mm > 0 else 10.0,
                              y_mm if y_mm > 0 else 10.0)

    max_span = 0.0
    for px, py in targets:
        max_span = max(max_span, math.hypot(px, py))
    for p in parts:
        max_span = max(max_span, math.hypot(p["x_mm"], p["y_mm"]))
    max_span = max(max_span, 1.0)
    avail = min(w, h) / 2.0 - 70  # margin for labels
    scale = avail / max_span

    def to_px(x_mm_, y_mm_):
        return cx + x_mm_ * scale, cy + y_mm_ * scale

    # Achsen
    mdc.SetPen(wx.Pen(wx.Colour(215, 215, 215), 1, wx.PENSTYLE_DOT))
    mdc.DrawLine(int(cx), 0, int(cx), int(h))
    mdc.DrawLine(0, int(cy), int(w), int(cy))

    # Aktuelle Löcher (graue Punkte)
    for p in parts:
        x, y = to_px(p["x_mm"], p["y_mm"])
        mdc.SetBrush(wx.Brush(wx.Colour(150, 150, 150)))
        mdc.SetPen(wx.Pen(wx.Colour(90, 90, 90), 1))
        mdc.DrawCircle(int(x), int(y), 4)

    # Zuordnung: jedes Loch zur nächstgelegenen freien Rasterposition
    # (greedy, wie bei 4 Löchern auf 4 Ecken — funktioniert, wenn die
    #  Löcher schon grob an ihren Zielorten liegen)
    targets_left = list(range(len(targets)))
    assignments = []  # (part, target_idx)
    for p in parts:
        best_t, best_d = None, None
        for ti in targets_left:
            tx, ty = targets[ti]
            d = (p["x_mm"] - tx) ** 2 + (p["y_mm"] - ty) ** 2
            if best_d is None or d < best_d:
                best_d, best_t = d, ti
        assignments.append((p, best_t))
        targets_left.remove(best_t)

    # Ziel-Boxen + Labels
    font = wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                   wx.FONTWEIGHT_NORMAL)
    mdc.SetFont(font)
    for i, (p, ti) in enumerate(assignments):
        tx, ty = targets[ti]
        bx, by = to_px(tx, ty)
        if i == 0:
            pen = wx.Pen(wx.Colour(200, 30, 30), 2)
            brush = wx.Brush(wx.Colour(255, 230, 230))
        else:
            pen = wx.Pen(wx.Colour(30, 90, 200), 2)
            brush = wx.Brush(wx.Colour(225, 238, 255))
        sz = 10
        mdc.SetPen(pen)
        mdc.SetBrush(brush)
        mdc.DrawRoundedRectangle(int(bx) - sz // 2, int(by) - sz // 2,
                                 sz, sz, 2)
        # Zuordnung: dünne Linie von aktueller Position zur Zielposition
        ax, ay = to_px(p["x_mm"], p["y_mm"])
        mdc.SetPen(wx.Pen(wx.Colour(150, 150, 150), 1, wx.PENSTYLE_DOT))
        mdc.DrawLine(int(ax), int(ay), int(bx), int(by))
        # Label neben der Zielposition
        tw, th = mdc.GetTextExtent(p["ref"])
        lx = int(bx) + 12 if tx >= 0 else int(bx) - 12 - tw
        tyy = int(by) - 18 if ty <= 0 else int(by) + 12
        mdc.SetTextForeground(wx.Colour(20, 20, 20))
        mdc.DrawText(p["ref"], lx, tyy)

    # Info
    mdc.SetTextForeground(wx.Colour(90, 90, 90))
    mdc.DrawText(f"{n} footprints selected", 6, 4)

    mdc.SelectObject(wx.NullBitmap)
    return bmp


class MountingHolesDialog(wx.Dialog):
    def __init__(self, parent, default_x, default_y, default_cols,
                 default_rows, parts, already_grouped=False):
        super().__init__(parent, title="Arrange Mounting Holes",
                         size=(700, 560))
        self._parts = list(parts)
        self._n = len(parts)

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        top = wx.BoxSizer(wx.HORIZONTAL)

        grid = wx.GridBagSizer(6, 8)

        def add_row(row, label, ctrl):
            grid.Add(wx.StaticText(panel, label=label), (row, 0),
                     flag=wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, (row, 1), flag=wx.EXPAND)

        self.cols = wx.SpinCtrl(panel, min=1, max=20, initial=default_cols)
        add_row(0, "Columns:", self.cols)

        self.rows = wx.SpinCtrl(panel, min=1, max=20, initial=default_rows)
        add_row(1, "Rows:", self.rows)

        self.x_spacing = wx.TextCtrl(panel, value=f"{default_x:.2f}")
        add_row(2, "X spacing [mm]:", self.x_spacing)

        self.y_spacing = wx.TextCtrl(panel, value=f"{default_y:.2f}")
        add_row(3, "Y spacing [mm]:", self.y_spacing)

        self.group = wx.CheckBox(panel,
                                 label="Group footprints after placing")
        self.group.SetValue(True)
        if already_grouped:
            self.group.SetValue(False)
            self.group.SetLabel("Group footprints after placing "
                                "(already grouped)")
            self.group.Disable()
        grid.Add(self.group, (4, 0), span=(1, 2))

        refs = ", ".join(p["ref"] for p in parts)
        grid.Add(wx.StaticText(panel,
                               label=f"{len(parts)} footprints selected: "
                                     f"{refs}"),
                 (5, 0), span=(1, 2), flag=wx.TOP)

        hint = ("Columns x Rows must equal the number of selected holes.\n"
                "Spacing = distance between adjacent holes.\n"
                "Each hole moves to its nearest free grid point.\n"
                "Grey dots = current position, boxes = target.\n"
                "No alignment to edge cuts.")
        grid.Add(wx.StaticText(panel, label=hint), (6, 0), span=(1, 2),
                 flag=wx.TOP)

        grid.AddGrowableCol(1)
        top.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        self.preview = wx.StaticBitmap(panel, size=(PREVIEW_PX, PREVIEW_PX))
        self.preview.SetMinSize((PREVIEW_PX, PREVIEW_PX))
        top.Add(self.preview, 0, wx.ALL | wx.ALIGN_CENTER, 10)

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

        for ctrl in (self.x_spacing, self.y_spacing):
            ctrl.Bind(wx.EVT_TEXT, self._on_change)
        for ctrl in (self.cols, self.rows):
            ctrl.Bind(wx.EVT_SPINCTRL, self._on_change)
        self._on_change()
        self.Layout()

    def _on_change(self, _evt=None):
        try:
            x = float(self.x_spacing.GetValue())
            y = float(self.y_spacing.GetValue())
        except ValueError:
            x = y = 0.0
        bmp = _render_preview(self._parts, self.cols.GetValue(),
                              self.rows.GetValue(), x, y)
        self.preview.SetBitmap(bmp)
        self.preview.Refresh()

    def get_values(self):
        try:
            x_mm = float(self.x_spacing.GetValue())
            y_mm = float(self.y_spacing.GetValue())
        except ValueError:
            return None
        cols = self.cols.GetValue()
        rows = self.rows.GetValue()
        if x_mm <= 0 or y_mm <= 0:
            return None
        if cols * rows != self._n:
            return None
        return {
            "x_mm": x_mm,
            "y_mm": y_mm,
            "cols": cols,
            "rows": rows,
            "group": self.group.IsChecked(),
        }


class ArrangeMountingHolesPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Arrange Mounting Holes"
        self.category = "Modify PCB"
        self.description = ("Arrange selected mounting holes on a grid "
                            "(columns x rows) with exact X/Y spacing. "
                            "Live preview included.")
        self.show_toolbar_button = False

    def Run(self):
        board = pcbnew.GetBoard()
        fps = [f for f in board.GetFootprints() if f.IsSelected()]

        if len(fps) < 2:
            wx.MessageBox(
                "Please select at least 2 mounting holes "
                "(4 or 6 are typical).",
                "Arrange Mounting Holes", wx.OK | wx.ICON_INFORMATION)
            return

        fps_sorted = sorted(fps, key=_ref_sort_key)
        n = len(fps_sorted)

        # Zentrum = Schwerpunkt der Auswahl
        cx = sum(f.GetPosition().x for f in fps) / len(fps)
        cy = sum(f.GetPosition().y for f in fps) / len(fps)

        parts = []
        for fp in fps_sorted:
            parts.append({
                "ref": fp.GetReference(),
                "x_mm": (fp.GetPosition().x - cx) / 1e6,
                "y_mm": (fp.GetPosition().y - cy) / 1e6,
            })

        # Default-Grid: Faktoren nahe sqrt(n) — Ausrichtung an BBox
        cols, rows = _default_grid(n)
        xs = [p["x_mm"] for p in parts]
        ys = [p["y_mm"] for p in parts]
        bw = max(xs) - min(xs)
        bh = max(ys) - min(ys)
        if cols > rows and bw < bh:
            cols, rows = rows, cols

        # Default-Spacing = BBox / (Faktoren-1)
        default_x = bw / (cols - 1) if cols > 1 and bw > 0 else 40.0
        default_y = bh / (rows - 1) if rows > 1 and bh > 0 else 40.0
        if default_x <= 0:
            default_x = 40.0
        if default_y <= 0:
            default_y = 40.0

        grouped = _already_grouped(fps_sorted)
        dlg = MountingHolesDialog(None, default_x, default_y, cols, rows,
                                  parts, already_grouped=grouped)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        vals = dlg.get_values()
        dlg.Destroy()
        if vals is None:
            wx.MessageBox(
                "Invalid input — columns x rows must equal the number of "
                "selected holes, spacing must be > 0.",
                "Arrange Mounting Holes", wx.OK | wx.ICON_ERROR)
            return

        center = pcbnew.VECTOR2I(int(cx), int(cy))
        targets = _grid_positions(vals["cols"], vals["rows"],
                                  vals["x_mm"], vals["y_mm"])

        # Zuordnung: nächstgelegene freie Rasterposition
        fps_unplaced = list(fps_sorted)
        placements = []  # (fp, offset_mm)
        targets_left = list(range(len(targets)))
        for fp in fps_unplaced:
            px = (fp.GetPosition().x - cx) / 1e6
            py = (fp.GetPosition().y - cy) / 1e6
            best_t, best_d = None, None
            for ti in targets_left:
                tx, ty = targets[ti]
                d = (px - tx) ** 2 + (py - ty) ** 2
                if best_d is None or d < best_d:
                    best_d, best_t = d, ti
            placements.append((fp, targets[best_t]))
            targets_left.remove(best_t)

        # Verschieben. Neue Gruppe nur anlegen, wenn die Footprints nicht
        # schon gemeinsam gruppiert sind (dann bleibt die bestehende Gruppe
        # erhalten — die Footprints werden durch SetPosition nicht
        # entgruppiert).
        group = None
        if vals["group"] and not grouped:
            group = pcbnew.PCB_GROUP(board)
            board.Add(group)

        for fp, (ox, oy) in placements:
            fp.SetPosition(pcbnew.VECTOR2I(
                int(center.x + ox * 1e6),
                int(center.y + oy * 1e6)))
            if group is not None:
                group.AddItem(fp)

        pcbnew.Refresh()


ArrangeMountingHolesPlugin().register()
