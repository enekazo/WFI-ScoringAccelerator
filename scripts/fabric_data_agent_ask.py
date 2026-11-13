"""One-shot attempt to query the published Fabric data agent via its MCP route, then the OpenAI-assistants route.

Entra token from `az` only. Records the result to docs/evidence/scenario-2-ontology/data_agent_answer.json.
"""
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

WS = "00000000-0000-4000-8000-000000000019"
AGENT = "00000000-0000-4000-8000-000000000024"
QUESTION = "Which crew types can reach parcel P003?"
OUT = Path("docs/evidence/scenario-2-ontology/data_agent_answer.json")


def token():
    return subprocess.check_output(
        "az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv",
        shell=True, text=True).strip()


def post(url, body, tok):
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read().decode()[:4000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:4000]


def main():
    tok = token()
    res = {"question": QUESTION, "routes": []}
    mcp = f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{WS}/dataagents/{AGENT}/agent"
    s, t = post(mcp, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, tok)
    res["routes"].append({"route": "fabric-mcp tools/list", "url": mcp, "status": s, "body": t})
    if s == 200:
        try:
            tools = json.loads(t.split("data:")[-1].strip())["result"]["tools"]
            name = tools[0]["name"]
            s2, t2 = post(mcp, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": name, "arguments": {"userQuestion": QUESTION}}}, tok)
            res["routes"].append({"route": "fabric-mcp tools/call", "tool": name, "status": s2, "body": t2})
        except Exception as e:
            res["routes"].append({"route": "fabric-mcp parse", "error": repr(e)})
    else:
        oa = (f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/aiskills/{AGENT}"
              "/aiassistant/openai/assistants?api-version=2024-05-01-preview")
        s3, t3 = post(oa, {"model": "not used", "name": "q"}, tok)
        res["routes"].append({"route": "openai assistants create", "url": oa, "status": s3, "body": t3})
    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2)[:3000])


if __name__ == "__main__":
    main()
