"""mcp_smoke.py — the MCP server over stdio as a client sees it: handshake, tools, one study with a picture.
Version: 2026.10.21.5
Usage (from the repository): python tools/mcp_smoke.py [--solver omc|ref]   (~10-20 s)
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main(solver):
    p = StdioServerParameters(command=sys.executable, args=["-m", "openmodelica_translator.mcp_server"],
                              env={**os.environ, "PYTHONPATH": str(ROOT)})
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo")
            print("server:", info.name, "| instructions:", len(init.instructions or ""), "chars")
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            ex = json.loads((ROOT / "examples" / "studies.json").read_text())
            st = dict(ex["dc_motor_pi_speed"], images=True)
            res = await s.call_tool("run_studies", {"studies": {"motor": st}, "solver": solver, "wait_s": 45})
            d = json.loads(res.content[0].text)
            if d.get("pending"):
                res = await s.call_tool("study_status", {"request": d["request"], "wait_s": 45})
                d = json.loads(res.content[0].text)
            m = d["studies"]["motor"]
            print("ok:", m["ok"], "reliable:", m.get("reliable"), "| derived:", json.dumps(m.get("derived"))[:300])
            print("checks:", {k: v.get("ok") for k, v in (m.get("checks") or {}).items()})
            if m.get("images"):
                img = await s.call_tool("get_image", {"path": m["images"]["outputs"]["path"]})
                print("image:", img.content[0].type, len(img.content[0].data), "bytes (base64)")


if __name__ == "__main__":
    solver = sys.argv[sys.argv.index("--solver") + 1] if "--solver" in sys.argv else "auto"
    asyncio.run(main(solver))
