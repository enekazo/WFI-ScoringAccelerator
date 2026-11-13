"""Draw Figure 4: cloud service placement for the grounding framework.

Microsoft service artwork: original icons in assets/service-icons; see NOTICE.md.

Usage: python scripts/make_fig02_architecture.py [output.png ...]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

S = 2
W, H = 1700, 1060
FD = Path(r"C:\Windows\Fonts")
ICONS = Path(__file__).parent / "assets" / "service-icons"


def font(size, bold=False, italic=False):
    name = "segoeuib.ttf" if bold else "segoeuii.ttf" if italic else "segoeui.ttf"
    return ImageFont.truetype(str(FD / name), int(size * S))


img = Image.new("RGB", (W * S, H * S), "white")
d = ImageDraw.Draw(img)
DARK, MUTED = "#1F2A37", "#6B7785"
GREY = "#A7AEB7"
BLUE, TEAL, PURPLE, ORANGE, GREYD = "#0078D4", "#0F7B6C", "#6B2FA8", "#B35A00", "#2D3A48"


def dashed(box, color, lw=2, dash=12, gap=8):
    x0, y0, x1, y1 = [v * S for v in box]
    for ax, ay, bx, by in [(x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)]:
        horiz = ay == by
        length = (bx - ax) if horiz else (by - ay)
        pos = 0
        while pos < length:
            end = min(pos + dash * S, length)
            seg = [(ax + pos, ay), (ax + end, ay)] if horiz else [(ax, ay + pos), (ax, ay + end)]
            d.line(seg, fill=color, width=lw * S)
            pos += (dash + gap) * S


def text(x, y, s, f, fill=DARK, anchor="la"):
    d.text((x * S, y * S), s, font=f, fill=fill, anchor=anchor)


def service_icon(name, x, y, size=44):
    with Image.open(ICONS / f"{name}.png") as source:
        artwork = ImageOps.contain(source.convert("RGBA"), (size * S, size * S))
        px = int(x * S + (size * S - artwork.width) / 2)
        py = int(y * S + (size * S - artwork.height) / 2)
        img.paste(artwork, (px, py), artwork)


def zone(x, y, w, h, title, sub, color, fill, icon=None):
    d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], 10 * S, fill=fill, outline=color, width=3 * S)
    offset = 48 if icon else 0
    if icon:
        service_icon(icon, x + 12, y + 16, 36)
    text(x + 16 + offset, y + 24, title, font(21, bold=True), color, "lm")
    text(x + 16 + offset, y + 50, sub, font(13), MUTED, "lm")


def card(x, y, w, h, title, body, color, design=False, icon=None):
    if design:
        d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], 6 * S, fill="#F7F8F9")
        dashed([x, y, x + w, y + h], GREY)
        head, tc, bc = GREY, "white", MUTED
        title += " (design)" if w > 300 else ""
    else:
        d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], 6 * S, fill="white", outline=color, width=2 * S)
        head, tc, bc = color, "white", DARK
    d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + 28) * S], 6 * S, fill=head)
    d.rectangle([x * S, (y + 14) * S, (x + w) * S, (y + 28) * S], fill=head)
    text(x + w / 2, y + 14, title, font(13.5, bold=True), tc, "mm")
    if icon:
        service_icon(icon, x + 14, y + 28 + (h - 28 - 44) / 2)
    lines = body.split("\n")
    top = y + 28 + (h - 28) / 2 - (len(lines) - 1) * 9
    for i, ln in enumerate(lines):
        text(x + w / 2 + (30 if icon else 0), top + i * 18, ln, font(12.5), bc, "mm")


def arrow(pts, color, label=None, lpos=None, dash=False):
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        if dash:
            n = int(max(abs(bx - ax), abs(by - ay)) / 14)
            for i in range(0, n, 2):
                p0 = (ax + (bx - ax) * i / n, ay + (by - ay) * i / n)
                p1 = (ax + (bx - ax) * min(i + 1, n) / n, ay + (by - ay) * min(i + 1, n) / n)
                d.line([(p0[0] * S, p0[1] * S), (p1[0] * S, p1[1] * S)], fill=color, width=3 * S)
        else:
            d.line([(ax * S, ay * S), (bx * S, by * S)], fill=color, width=3 * S)
    (ax, ay), (bx, by) = pts[-2], pts[-1]
    dx, dy = (bx - ax), (by - ay)
    n = (dx * dx + dy * dy) ** 0.5 or 1
    ux, uy = dx / n, dy / n
    tip = (bx * S, by * S)
    l = (bx - ux * 12 - uy * 6, by - uy * 12 + ux * 6)
    r = (bx - ux * 12 + uy * 6, by - uy * 12 - ux * 6)
    d.polygon([tip, (l[0] * S, l[1] * S), (r[0] * S, r[1] * S)], fill=color)
    if label:
        lx, ly = lpos
        text(lx, ly, label, font(12, bold=True), color, "mm")


text(10, 8, "WFI Pinned Scoring Accelerator on Azure", font(30, bold=True))
text(10, 52, "Our grounding framework: recommendation on Azure | evidence and authority on Microsoft Fabric",
     font(15), MUTED)

text(10, 90, "Cloud architecture being built. Service placement and connections are shown, not live-deployment status.",
     font(13), MUTED)

zone(10, 120, 820, 690, "Azure resource group", "Recommendation plane", BLUE, "#EEF5FC", icon="resource-group")
card(35, 205, 350, 150, "Microsoft Foundry", "Foundry Agent Service\nagent and MCP connections", BLUE, icon="foundry")
card(465, 205, 340, 150, "Azure Function | MCP tool host",
     "Pinned policy\nDeterministic scorer\nReceipts\nVerifier", BLUE, icon="functions")
arrow([(385, 275), (465, 275)], BLUE)
text(425, 249, "MCP", font(12, bold=True), BLUE, "mm")

card(35, 410, 350, 80, "Model deployment", "Model used by the\nFoundry agent", BLUE, icon="models")
card(465, 410, 340, 80, "Azure Blob Storage", "Policy and receipt blobs", BLUE, icon="storage")
arrow([(210, 355), (210, 410)], BLUE)
arrow([(635, 355), (635, 410)], BLUE)

zone(900, 120, 790, 690, "Microsoft Fabric workspace", "Evidence plane and authority reference implementation", TEAL, "#ECF6F4", icon="fabric")
service_icon("lakehouse", 925, 190, 32)
text(970, 207, "Evidence plane | Fabric Lakehouse / OneLake", font(17, bold=True), TEAL, "lm")
card(925, 245, 220, 100, "Bronze", "Raw evidence", TEAL)
card(1185, 245, 220, 100, "Silver", "Cleaned and labelled\nmeasured / estimated", TEAL)
card(1445, 245, 220, 100, "Gold", "Scored receipts\nDelta tables", TEAL)
arrow([(1145, 295), (1185, 295)], TEAL)
arrow([(1405, 295), (1445, 295)], TEAL)

card(925, 395, 740, 100, "Fabric IQ ontology and graph",
     "Business entities and relationships bound to Lakehouse tables", TEAL, icon="graph")
arrow([(35, 325), (22, 325), (22, 555), (865, 555), (865, 445), (925, 445)], TEAL)
text(540, 535, "Separate Foundry MCP connection to Fabric IQ", font(13, bold=True), TEAL, "mm")

text(925, 600, "Authority plane | reference implementation on Fabric", font(17, bold=True), ORANGE, "lm")
card(925, 635, 340, 105, "Human review and approval",
     "Propose, review, approve\nNamed, authenticated owners", ORANGE)
card(1325, 635, 340, 105, "authority_decisions",
     "Lakehouse Delta table\nHash-chained decision log", ORANGE, icon="lakehouse")
arrow([(1265, 690), (1325, 690)], ORANGE)
text(1295, 775, "Budget allocation and operational execution remain design only.", font(13), MUTED, "mm")

card(35, 650, 770, 90, "Application Insights", "Tracing across agent, model and tool calls", PURPLE, icon="insights")
text(420, 775, "The agent proposes; approval stays with people.", font(14, italic=True), BLUE, "mm")

d.rounded_rectangle([10 * S, 850 * S, 1690 * S, 920 * S], 8 * S, fill="#F3EDF9", outline=PURPLE, width=3 * S)
text(30, 885, "Microsoft Entra ID", font(18, bold=True), PURPLE, "lm")
text(270, 885, "Authentication and scoped access across Foundry, Azure Functions and Fabric", font(15), DARK, "lm")

text(10, 965, "One framework, three responsibilities", font(20, bold=True))
text(10, 1005, "Recommendation: Foundry + Azure Functions     |     Evidence: Fabric Lakehouse + Fabric IQ     |     Authority: Fabric reference implementation",
     font(14), MUTED)

out = sys.argv[1:] or [str(Path("docs") / "dissemination" / "figures" / "fig02_azure_architecture.png")]
for o in out:
    Path(o).parent.mkdir(parents=True, exist_ok=True)
    img.save(o)
    print("wrote", o)
