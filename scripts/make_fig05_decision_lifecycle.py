"""Render the authority reference path and distinguish planned operational stages."""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
S = 2
W, H = 1700, 770
BLUE, ORANGE, RED = "#0078D4", "#B35A00", "#B42318"
DARK, MUTED, GREY = "#1F2A37", "#6B7785", "#A7AEB7"
image = Image.new("RGB", (W * S, H * S), "white")
draw = ImageDraw.Draw(image)
elements = []


def font(size, bold=False):
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(str(Path(r"C:\Windows\Fonts") / name), int(size * S))


def label(x, y, value, size=15, bold=False, colour=DARK, centred=False):
    face = font(size, bold)
    lines = value.split("\n")
    line_height = size * 1.4
    for i, line in enumerate(lines):
        draw.text((int(x * S), int((y + i * line_height) * S)), line,
                  font=face, fill=colour, anchor="mt" if centred else "lt")
    width = max(draw.textlength(line, font=face) / S for line in lines) + 10
    elements.append({
        "id": f"text-{len(elements)}", "type": "text",
        "x": x - width / 2 if centred else x, "y": y,
        "width": width, "height": size * 2.5 * len(lines),
        "text": value, "originalText": value, "fontSize": size, "fontFamily": 2,
        "strokeColor": "#000000", "backgroundColor": "transparent",
        "textAlign": "center" if centred else "left", "verticalAlign": "top",
    })


def dashed(x, y, w, h, colour=GREY):
    for a, b in [((x, y), (x + w, y)), ((x, y + h), (x + w, y + h)),
                 ((x, y), (x, y + h)), ((x + w, y), (x + w, y + h))]:
        length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
        for offset in range(0, int(length), 18):
            end = min(offset + 10, length)
            p = (a[0] + (b[0] - a[0]) * offset / length,
                 a[1] + (b[1] - a[1]) * offset / length)
            q = (a[0] + (b[0] - a[0]) * end / length,
                 a[1] + (b[1] - a[1]) * end / length)
            draw.line([(int(p[0] * S), int(p[1] * S)),
                       (int(q[0] * S), int(q[1] * S))], fill=colour, width=2 * S)


def box(x, y, w, h, title, body, colour=BLUE, planned=False, status=None):
    fill = "#F7F8F9" if planned else "white"
    border = GREY if planned else colour
    draw.rounded_rectangle((x * S, y * S, (x + w) * S, (y + h) * S),
                           radius=7 * S, fill=fill,
                           outline=None if planned else border, width=2 * S)
    if planned:
        dashed(x, y, w, h)
    elements.append({
        "id": f"box-{len(elements)}", "type": "rectangle",
        "x": x, "y": y, "width": w, "height": h, "strokeColor": border,
        "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeStyle": "dashed" if planned else "solid", "strokeWidth": 2,
        "roundness": {"type": 3},
    })
    label(x + w / 2, y + 14, title, 17, True,
          MUTED if planned else colour, centred=True)
    if status:
        label(x + w / 2, y + 46, status, 11, True, MUTED, centred=True)
    label(x + w / 2, y + (78 if status else 50), body, 14,
          colour=MUTED if planned else DARK, centred=True)


def arrow(x1, y1, x2, y2, colour=DARK, planned=False):
    if planned:
        length = max(abs(x2 - x1), abs(y2 - y1))
        for offset in range(0, int(length), 16):
            end = min(offset + 8, length)
            p = (x1 + (x2 - x1) * offset / length, y1 + (y2 - y1) * offset / length)
            q = (x1 + (x2 - x1) * end / length, y1 + (y2 - y1) * end / length)
            draw.line([(int(p[0] * S), int(p[1] * S)),
                       (int(q[0] * S), int(q[1] * S))], fill=colour, width=2 * S)
    else:
        draw.line((x1 * S, y1 * S, x2 * S, y2 * S), fill=colour, width=2 * S)
    if y1 == y2:
        tip = [(x2, y2), (x2 - 9, y2 - 5), (x2 - 9, y2 + 5)]
    else:
        tip = [(x2, y2), (x2 - 5, y2 - 9), (x2 + 5, y2 - 9)]
    draw.polygon([(x * S, y * S) for x, y in tip], fill=colour)
    elements.append({
        "id": f"arrow-{len(elements)}", "type": "arrow",
        "x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1,
        "points": [[0, 0], [x2 - x1, y2 - y1]], "strokeColor": colour,
        "strokeWidth": 2, "strokeStyle": "dashed" if planned else "solid",
        "endArrowhead": "arrow",
    })


label(20, 18, "Where the decision stops", 30, True)
label(20, 64, "The Fabric reference implementation reaches approval, not allocation or operational execution.", 17, colour=MUTED)
label(20, 112, "COLOURED: built and tested reference path", 14, True, BLUE)
label(620, 112, "GREY / DASHED: design only, not built", 14, True, MUTED)

stages = [
    ("1  Check evidence", "Receipt hash and status\nFreshness\nNarration verification", False),
    ("2  Propose", "Foundry agent submits\na proposal tied\nto the receipt", False),
    ("3  Review", "Named reviewer\nadvances or rejects\nthe proposal", False),
    ("4  Approve", "Named approver\nrecords approval\nSingle-person live run", False),
    ("5  Allocate", "Assign budget,\ncrews and an\nexecution window", True),
    ("6  Execute", "Carry out the\napproved work\nin the field", True),
    ("7  Replay", "Reconstruct the full\npath from evidence\nto operational action", True),
]
for i, (title, body, planned) in enumerate(stages):
    x = 20 + i * 240
    box(x, 190, 220, 190, title, body, planned=planned,
        status="DESIGN ONLY" if planned else "REFERENCE IMPLEMENTATION")
    if i < len(stages) - 1:
        arrow(x + 220, 285, x + 240, 285,
              colour=GREY if i >= 3 else DARK, planned=i >= 3)

box(20, 445, 940, 100, "Blocked: no progression",
    "Stale or incomplete receipt | narration mismatch | unauthorised actor | broken hash chain",
    colour=RED)
for x in [130, 610, 850]:
    arrow(x, 380, x, 445, RED)

box(20, 585, 940, 100, "Built: Fabric decision log",
    "Append-only, hash-chained Delta table records proposals, reviews and approvals.\nHash-chain verification detects tampering; it is not a full operational replay.",
    colour=ORANGE)
box(1020, 445, 660, 240, "Planned: improvement reporting",
    "Aggregate block reasons and approval delays\ninto operational improvement reports.\n\nThis reporting workflow is not built.", planned=True)

label(20, 720, "Live evidence: one Entra identity reviewed and approved in explicit single_person mode; no two-person approval is claimed.",
      15, colour=MUTED)

source = ROOT / "docs" / "dissemination" / "excalidraw" / "fig05_decision_lifecycle.excalidraw"
source.write_text(json.dumps({
    "type": "excalidraw", "version": 2, "source": "WFI Pinned Scoring Accelerator",
    "elements": elements, "appState": {"viewBackgroundColor": "#ffffff"},
}, indent=2), encoding="utf-8")
outputs = sys.argv[1:] or [
    str(ROOT / "docs" / "dissemination" / "figures" / "fig05_decision_lifecycle.png"),
]
for output in outputs:
    image.save(output)
    print("wrote", output)
