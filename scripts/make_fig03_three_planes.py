"""Draw Figure 3 (three planes) with built parts in colour and design-only parts greyed.

Usage: python scripts/make_fig03_three_planes.py [output.png ...]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 2
W, H = 1700, 1010
FD = "C:/Windows/Fonts/"


def font(size, bold=False, italic=False):
    name = "segoeuib.ttf" if bold else "segoeuii.ttf" if italic else "segoeui.ttf"
    return ImageFont.truetype(FD + name, int(size * S))


img = Image.new("RGB", (W * S, H * S), "white")
d = ImageDraw.Draw(img)

DARK, MUTED = "#1F2A37", "#6B7785"
GREY_HEAD, GREY_LINE, GREY_TXT = "#A7AEB7", "#A7AEB7", "#6B7785"


def rect(x, y, w, h, fill, line, lw=2, r=8, dashed=False):
    box = [x * S, y * S, (x + w) * S, (y + h) * S]
    if dashed:
        d.rounded_rectangle(box, r * S, fill=fill)
        dashed_border(box, line, lw * S)
    else:
        d.rounded_rectangle(box, r * S, fill=fill, outline=line, width=lw * S)


def dashed_border(box, color, lw, dash=14, gap=9):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in [(x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)]:
        horiz = ay == by
        length = (bx - ax) if horiz else (by - ay)
        pos = 0
        while pos < length:
            end = min(pos + dash * S, length)
            if horiz:
                d.line([(ax + pos, ay), (ax + end, ay)], fill=color, width=lw)
            else:
                d.line([(ax, ay + pos), (ax, ay + end)], fill=color, width=lw)
            pos += (dash + gap) * S


def text(x, y, s, f, fill=DARK, anchor="la"):
    d.text((x * S, y * S), s, font=f, fill=fill, anchor=anchor)


def card(x, y, title, body, color, state):
    """state: built | partly | design"""
    w, h = 440, 98
    if state == "design":
        rect(x, y, w, h, "#F7F8F9", GREY_LINE, 2, dashed=True)
        d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + 34) * S], 8 * S, fill=GREY_HEAD)
        d.rectangle([x * S, (y + 17) * S, (x + w) * S, (y + 34) * S], fill=GREY_HEAD)
        tag = "DESIGN ONLY"
        body_fill = GREY_TXT
    else:
        rect(x, y, w, h, "white", color, 2)
        d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + 34) * S], 8 * S, fill=color)
        d.rectangle([x * S, (y + 17) * S, (x + w) * S, (y + 34) * S], fill=color)
        tag = "PARTLY BUILT" if state == "partly" else None
        body_fill = DARK
    text(x + w / 2, y + 17, title, font(15, bold=True), "white", "mm")
    if tag:
        text(x + w - 12, y + 17, tag, font(9.5, bold=True), "white", "rm")
    lines = body.split("\n")
    top = y + 66 - (len(lines) - 1) * 10
    for i, ln in enumerate(lines):
        text(x + w / 2, top + i * 20, ln, font(14), body_fill, "mm")


def plane(x, title, sub, color, fill, foot, cards):
    rect(x, 120, 500, 640, fill, color, 3, 10)
    text(x + 16, 146, title, font(22, bold=True), color, "lm")
    text(x + 16, 172, sub, font(14), MUTED, "lm")
    for i, (t, b, st) in enumerate(cards):
        card(x + 30, 232 + i * 118, t, b, color, st)
    text(x + 250, 731, foot, font(14, italic=True), color, "mm")


text(10, 6, "Three planes, three homes", font(30, bold=True), DARK)
text(10, 48, "The agent reads from controlled data and can only propose; nothing executes without passing controlled transitions",
     font(17), MUTED)

# legend
rect(10, 84, 14, 14, "#0078D4", "#0078D4", 1, 3)
text(32, 91, "Built and tested in this accelerator", font(14), DARK, "lm")
rect(330, 84, 14, 14, "#F7F8F9", GREY_LINE, 2, 3, dashed=True)
text(352, 91, "Design only, not built", font(14), DARK, "lm")

plane(10, "Recommendation plane", "Microsoft Azure and Foundry: where the agent works", "#0078D4", "#EEF5FC", "Advisory only: it can propose, never decide", [
    ("Code scorer", "policy-locked rules compute the score", "built"),
    ("Hash-locked receipt", "score, band, status, provenance", "built"),
    ("Foundry agent", "explains through read-only tools", "built"),
    ("Verifier", "checks every claim against the receipt", "built"),
])
plane(600, "Authority plane", "Reference implementation on Fabric; the rest is design", "#B35A00", "#FDF6EE", "Confidence never advances a state", [
    ("Review and approval", "propose, review, approve by named,\nauthenticated owners", "built"),
    ("Budget and allocation", "explicit workflow states", "design"),
    ("Execution readiness", "stale or incomplete evidence\nblocks a proposal", "partly"),
    ("Execute and log", "tamper-evident hash chain\n(execution itself is design only)", "partly"),
])
plane(1190, "Evidence plane", "Microsoft Fabric OneLake: where the data comes from", "#0F7B6C", "#ECF6F4", "Missing or stale evidence blocks", [
    ("Bronze", "raw evidence", "built"),
    ("Silver", "cleaned, labelled measured / estimated", "built"),
    ("Gold", "scored receipts (Delta tables)", "built"),
    ("Replayable read models", "reporting and review", "design"),
])

# arrows
def arrow(x0, x1, y, color, label, ly):
    d.line([(x0 * S, y * S), (x1 * S, y * S)], fill=color, width=3 * S)
    sgn = 1 if x1 > x0 else -1
    d.polygon([(x1 * S, y * S), ((x1 - 11 * sgn) * S, (y - 6) * S), ((x1 - 11 * sgn) * S, (y + 6) * S)], fill=color)
    text((x0 + x1) / 2, ly, label, font(13), color, "mm")


arrow(510, 600, 460, "#0078D4", "recommends", 436)
arrow(1190, 1100, 460, "#0F7B6C", "evidence", 436)

# Entra
rect(10, 790, 1680, 65, "#F4EEFA", "#6B2FA8", 2, 8)
text(30, 822, "Microsoft Entra ID", font(19, bold=True), "#6B2FA8", "lm")
text(300, 822, "separates user and service identities and scopes roles across all three planes; no API keys",
     font(16), DARK, "lm")

# design goal
rect(350, 875, 1000, 75, "#F7F8F9", GREY_LINE, 2, 8, dashed=True)
text(850, 900, "DESIGN GOAL, NOT BUILT", font(11, bold=True), GREY_TXT, "mm")
text(850, 928, "Safe to act only when all three planes name the same intervention, location scope and execution window",
     font(16, bold=True), GREY_TXT, "mm")

text(850, 985, "Built: the agent can only propose; the evidence gate and the review and approval steps are tested.",
     font(14), MUTED, "mm")

outs = sys.argv[1:] or ["docs/dissemination/figures/fig03_three_planes.png"]
for o in outs:
    Path(o).parent.mkdir(parents=True, exist_ok=True)
    img.save(o)
    print("wrote", o, img.size)
