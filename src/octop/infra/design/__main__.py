"""Entry point for the standalone geometry kernel.

python -m octop.infra.design                 # REST on 127.0.0.1:8848
python -m octop.infra.design --mcp           # MCP  on 127.0.0.1:8849
python -m octop.infra.design --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import argparse

from octop.infra.design import mcp_server, server


def main() -> None:
    parser = argparse.ArgumentParser(prog="octop-design-kernel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8848)
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="serve the MCP endpoint instead of the REST API",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=8849,
        help="port for the MCP server (ignored without --mcp)",
    )
    args = parser.parse_args()

    if args.mcp:
        mcp_server.serve(args.host, args.mcp_port)
        return
    server.serve(args.host, args.port)


if __name__ == "__main__":
    main()
