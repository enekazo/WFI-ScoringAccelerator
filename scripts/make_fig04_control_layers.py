"""Render legacy evidence labels or V10 deployment-status labels for control layers."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--v10", action="store_true",
                    help="Render deployment statuses to V10-only assets, preserving older figures.")
args = parser.parse_args()
S = 2
W, H = 1800, 1210
DARK, MUTED, BLUE = "#1F2A37", "#6B7785", "#0078D4"
GREEN, GREY = "#107C10", "#A7AEB7"
image = Image.new("RGB", (W * S, H * S), "white")
draw = ImageDraw.Draw(image)
elements = []


def font(size, bold=False):
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(str(Path(r"C:\Windows\Fonts") / name), int(size * S))


def text(x, y, value, size=14, bold=False, colour=DARK):
    if args.v10:
        colour = "#000000"
    face = font(size, bold)
    lines = value.split("\n")
    for i, line in enumerate(lines):
        draw.text((int(x * S), int((y + i * size * 1.5) * S)),
                  line, fill=colour, font=face, anchor="lt")
    elements.append({
        "id": f"text-{len(elements)}", "type": "text", "x": x, "y": y,
        "width": max(draw.textlength(line, font=face) / S for line in lines) + 10,
        "height": size * 2.5 * len(lines), "text": value, "originalText": value,
        "fontSize": size, "fontFamily": 2, "strokeColor": "#000000",
        "backgroundColor": "transparent", "textAlign": "left", "verticalAlign": "top",
    })


def status(x, y, value, kind):
    colour, fill = {
        "shown": (GREEN, "#EAF5E8"),
        "code": ("#A75B00", "#FFF6E9") if args.v10 else (BLUE, "#EEF5FC"),
        "planned": (GREY, "#F3F4F5"),
        "limit": (MUTED, "#FFFFFF"),
    }[kind]
    draw.rounded_rectangle((x * S, y * S, (x + 260) * S, (y + 31) * S),
                           radius=5 * S, fill=fill, outline=colour, width=S)
    elements.append({
        "id": f"status-{len(elements)}", "type": "rectangle",
        "x": x, "y": y, "width": 260, "height": 31,
        "strokeColor": colour, "backgroundColor": "transparent",
        "strokeWidth": 1, "roundness": {"type": 3},
    })
    text(x + 10, y + 7, value, 12, True, MUTED if kind == "planned" else colour)


text(20, 20, "Control layers: deployment status" if args.v10
     else "Control layers: what is demonstrated, implemented or planned", 29, True)
text(20, 66, "Microsoft services provide the platform; the accelerator adds policy, receipt and decision checks.", 17, colour=MUTED)
if args.v10:
    text(20, 113, "GREEN: Completed = deployed or configured", 14, True, GREEN)
    text(760, 113, "GREY: Planned = not yet deployed", 14, True, MUTED)
else:
    text(20, 113, "GREEN: demonstrated reference capability", 14, True, GREEN)
    text(600, 113, "BLUE: code/tested; cloud verification pending", 14, True, BLUE)
    text(1240, 113, "GREY: planned or not implemented", 14, True, MUTED)

columns = [20, 85, 350, 720, 1140, 1510]
for x, heading in zip(columns, ["#", "Layer", "What is controlled", "Microsoft service", "Accelerator code",
                               "Status" if args.v10 else "Evidence / status"]):
    text(x, 177, heading, 15, True)

rows = [
    ("Data", "Labelled, traceable evidence",
     "Fabric Lakehouse / OneLake\nBlob Storage for cloud receipt copies",
     "Provenance labels\nReceipt-hash validation",
     [("Fabric: demonstrated", "shown"), ("Blob receipts: live verified", "shown")]),
    ("Score", "Policy-controlled scores",
     "Fabric Gold receipts\nBlob copies for cloud tools",
     "Pinned policy and scorer\nHash-locked receipts",
     [("Scoring: demonstrated", "shown"), ("Function scoring: live pending", "code")]),
    ("Agent", "Receipt tools can read,\nnot mutate evidence",
     "Foundry Agent Service\nAzure Function MCP receipt host",
     "Allow-listed read operations\nNo authority writes on receipt MCP",
     [("Foundry / IQ: demonstrated", "shown"), ("Receipt MCP: live verified", "shown")]),
    ("Verification", "Narration and receipts checked;\ntracing records activity",
     "Application Insights: tracing\nFoundry evaluation: planned",
     "Narration verifier\nReceipt and policy hash checks",
     [("Verifier + trace: reference shown", "shown"), ("Function verifier: live pending", "code")]),
    ("Identity / access", "Who can access each service",
     "Microsoft Entra ID\nManaged identity + Azure RBAC",
     "Secret-scan test",
     [("User auth: demonstrated", "shown"), ("Receipt RBAC: live verified", "shown")]),
    ("Agent governance", "Agent inventory, ownership\nand platform oversight",
     "Agent 365 / Foundry Control Plane\nEntra Agent ID, Defender, Purview",
     "Platform configuration\nNo live rollout claimed",
     [("Design only", "planned")]),
    ("Authority", "People review and approve",
     "Fabric Lakehouse Delta decision log\nEntra-authenticated actor identity",
     "Configured reviewer / approver roles\nAgent cannot review or approve",
     [("Reference path: demonstrated", "shown"), ("One identity; not two-person", "limit")]),
    ("Cost", "Estimate token usage;\ncheck a per-run budget",
     "Azure budgets / cost alerts: planned\nFoundry quotas / Fabric capacity",
     "Estimated token-cost meter\nPer-run budget guard + kill switch",
     [("Run controls: code/tested", "code"), ("Azure budgets: planned", "planned")]),
]

if args.v10:
    current_statuses = [
        [("Completed", "shown")],
        [("Completed", "shown")],
        [("Completed", "shown")],
        [("Completed", "shown")],
        [("Completed", "shown")],
        [("Planned", "planned")],
        [("Completed", "shown")],
        [("Planned", "planned")],
    ]
    rows = [(*row[:4], statuses) for row, statuses in zip(rows, current_statuses, strict=True)]

for i, (layer, controlled, service, code, statuses) in enumerate(rows):
    y = 215 + i * 105
    if i % 2 == 0:
        draw.rectangle((20 * S, y * S, 1780 * S, (y + 105) * S), fill="#F5F7F9")
    draw.line((20 * S, y * S, 1780 * S, y * S), fill="#CFD7DF", width=S)
    text(columns[0] + 8, y + 33, str(i + 1), 15, True)
    text(columns[1], y + 33, layer, 16, True)
    text(columns[2], y + 24, controlled)
    text(columns[3], y + 24, service, colour=MUTED if i == 5 else BLUE)
    text(columns[4], y + 24, code)
    for j, (value, kind) in enumerate(statuses):
        status(columns[5], y + (33 if args.v10 else 16) + j * 38, value, kind)

draw.line((20 * S, 1055 * S, 1780 * S, 1055 * S), fill="#CFD7DF", width=S)
text(20, 1090, "Status describes deployment, not production readiness. Optional Foundry evaluation and platform governance remain planned."
     if args.v10 else
     "A green label applies only to the named reference capability, not to an end-to-end production deployment.", 15, colour=MUTED)
text(20, 1128, "Full trace capture is not verified. Approval is a single-person reference workflow; its permissions are application roles, not Entra RBAC."
     if args.v10 else
     "Tracing is not answer verification. Application roles are not Entra RBAC roles. The per-run guard is not a total Azure/Fabric spend cap.",
     15, colour=MUTED)
text(20, 1166, "Run-cost guard and kill switch: local tests only, not deployed cloud-wide. No total Azure/Fabric spend cap is claimed."
     if args.v10 else
     "Receipt MCP, Blob access and runtime RBAC: live-verified baseline. Function scoring and answer verification: live acceptance pending.", 15, colour=MUTED)

output = ROOT / "docs" / "dissemination" / "figures"
if args.v10:
    output /= "v10"
    output.mkdir(parents=True, exist_ok=True)
    source = output / "fig04_control_layers.excalidraw"
else:
    source = ROOT / "docs" / "dissemination" / "excalidraw" / "fig04_control_layers.excalidraw"
source.write_text(json.dumps({
    "type": "excalidraw", "version": 2, "source": "WFI Pinned Scoring Accelerator",
    "elements": elements, "appState": {"viewBackgroundColor": "#ffffff"},
}, indent=2), encoding="utf-8")
image.save(output / "fig04_control_layers.png")
print("wrote", output / "fig04_control_layers.png")
