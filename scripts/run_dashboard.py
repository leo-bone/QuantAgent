#!/usr/bin/env python3
"""Launch QuantAgent system: API server + Dashboard.

Usage:
    python3 scripts/run_dashboard.py              # Dashboard only (port 8050)
    python3 scripts/run_dashboard.py --with-api     # API + Dashboard (shared agent)
    python3 scripts/run_dashboard.py --port 8080  # Custom port
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Clear proxy settings that block RPC/API calls ───
if not os.environ.get("QUANTAGENT_KEEP_PROXY"):
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)
    os.environ["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"

from loguru import logger

from config.settings import settings


def run_api_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the FastAPI server in a thread."""
    import uvicorn
    from quantagent.api.app import app as api_app

    logger.info(f"🚀 API server starting at http://{host}:{port}")
    uvicorn.run(api_app, host=host, port=port, log_config=None)


def run_dashboard_server(
    host: str = "0.0.0.0", port: int = 8050, debug: bool = True,
    agent_ref=None, api_base_url: str = "http://127.0.0.1:8000",
) -> None:
    """Run the Dash dashboard server."""
    from quantagent.dashboard.app import create_dashboard

    logger.info(f"📊 Dashboard starting at http://{host}:{port}")
    app = create_dashboard(agent_ref=agent_ref, api_base_url=api_base_url)
    app.run(host=host, port=port, debug=debug, use_reloader=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QuantAgent Dashboard")
    parser.add_argument("--with-api", action="store_true", help="Also start the API server (shared agent instance)")
    parser.add_argument("--api-port", type=int, default=8000, help="API server port (default: 8000)")
    parser.add_argument("--port", type=int, default=8050, help="Dashboard port (default: 8050)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--no-debug", action="store_true", help="Disable debug mode")
    args = parser.parse_args()

    logger.info(f"QuantAgent Dashboard Launcher | Mode: {settings.trading_mode.upper()}")

    agent_ref = None
    api_base_url = f"http://{args.host}:{args.api_port}"

    if args.with_api:
        # Start API server in background thread
        # The API server initializes its own agent instance
        api_thread = threading.Thread(
            target=run_api_server, args=(args.host, args.api_port), daemon=True,
        )
        api_thread.start()
        logger.info(f"✅ API thread started (http://{args.host}:{args.api_port})")

        # Wait for API to be ready and get the agent reference
        import time
        for attempt in range(10):
            time.sleep(1)
            try:
                from quantagent.api.app import get_agent
                agent_ref = get_agent()
                logger.info("✅ Agent reference obtained from API server")
                break
            except RuntimeError:
                logger.debug(f"Waiting for agent initialization... (attempt {attempt + 1})")
    else:
        # No API — Dashboard uses its own data sources
        # If agent_ref is None, Dashboard will try API endpoints (which won't work)
        # and fall back to empty state gracefully
        api_base_url = "http://127.0.0.1:8000"  # Default, may not be running

    # Run dashboard in main thread (required by Dash)
    run_dashboard_server(
        host=args.host, port=args.port, debug=not args.no_debug,
        agent_ref=agent_ref, api_base_url=api_base_url,
    )


if __name__ == "__main__":
    main()
