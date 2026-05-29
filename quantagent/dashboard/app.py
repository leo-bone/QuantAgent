"""QuantAgent Dashboard - Real-time monitoring and control interface.

Dash + Plotly based dashboard with:
- Agent status overview (KPI cards)
- K-line chart with signal annotations (SOL/BNB)
- Signal monitor (technical/on-chain/LLM gauges)
- Trade history and P&L curve
- Chain health status
- Agent decision log

Data flow: agent_ref (in-memory) → api_base_url (HTTP) → empty fallback
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import dash
from dash import dcc, html, Input, Output, State, callback, ctx
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests as http_requests


# ─── App Factory ───

def create_dashboard(
    agent_ref=None,
    api_base_url: str = "http://127.0.0.1:8000",
) -> dash.Dash:
    """Create and configure the Dash application."""
    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.DARKLY],
        suppress_callback_exceptions=True,
        title="QuantAgent Dashboard",
        update_title="Updating...",
    )

    app.layout = _build_layout()
    _register_callbacks(app, agent_ref, api_base_url)

    return app


# ─── Color Palette (Chinese convention: 涨=绿, 跌=红) ───

COLORS = {
    "bg": "#0f0f1a",
    "card_bg": "#1a1a2e",
    "border": "#2a2a4a",
    "text": "#e0e0e0",
    "text_muted": "#8888aa",
    "bullish": "#00e676",     # 绿色=涨
    "bearish": "#ff1744",     # 红色=跌
    "neutral": "#ffab00",     # 琥珀色
    "accent": "#448aff",      # 蓝色强调
    "solana": "#9945ff",
    "bsc": "#f0b90b",
    "volume_up": "rgba(0,230,118,0.3)",
    "volume_down": "rgba(255,23,68,0.3)",
}

SIGNAL_COLORS = {"bullish": COLORS["bullish"], "bearish": COLORS["bearish"], "neutral": COLORS["neutral"]}
CHAIN_COLORS = {"solana": COLORS["solana"], "bsc": COLORS["bsc"]}


# ─── Data Access Helpers ───

# No-proxy settings: bypass Clash/system proxy for localhost calls
_NO_PROXY = {"no_proxy": True}


def _api_get(api_base_url: str, path: str, timeout: float = 3.0) -> Optional[dict]:
    """Safely call API endpoint. Returns dict or None."""
    try:
        resp = http_requests.get(f"{api_base_url}{path}", timeout=timeout, proxies=_NO_PROXY)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _api_post(api_base_url: str, path: str, timeout: float = 3.0) -> Optional[dict]:
    """Safely call API POST endpoint. Returns dict or None."""
    try:
        resp = http_requests.post(f"{api_base_url}{path}", timeout=timeout, proxies=_NO_PROXY)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _get_agent_trades(agent_ref, limit: int = 50) -> list[dict]:
    """Get trade history from agent in-memory or API."""
    if agent_ref is not None:
        try:
            return agent_ref.get_recent_trades(limit=limit)
        except Exception:
            pass
    return []


def _get_agent_decisions(agent_ref, limit: int = 20) -> list:
    """Get recent decisions from agent memory."""
    if agent_ref is not None:
        try:
            return agent_ref.memory.get_recent_decisions(limit=limit)
        except Exception:
            pass
    return []


def _get_live_prices(api_base_url: str, tokens: list[str] = None) -> dict:
    """Get live token prices. Returns {token: price}."""
    if tokens is None:
        tokens = ["SOL", "BNB"]
    data = _api_get(api_base_url, f"/prices?tokens={','.join(tokens)}")
    if data and isinstance(data, dict):
        return data
    return {}


# ─── Layout Components ───

def _stat_card(title: str, value_id: str, subtitle_id: str = "", icon: str = "📊") -> dbc.Card:
    children = [
        html.Div([
            html.Span(icon, style={"fontSize": "1.5rem", "marginRight": "8px"}),
            html.Span(title, className="text-muted", style={"fontSize": "0.85rem"}),
        ]),
        html.H4(id=value_id, children="--", className="mt-2 mb-0", style={"fontWeight": "700"}),
    ]
    if subtitle_id:
        children.append(html.Small(id=subtitle_id, children="", className="text-muted"))
    return dbc.Card(
        dbc.CardBody(children),
        style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['border']}", "borderRadius": "12px"},
    )


def _build_header() -> dbc.Navbar:
    return dbc.Navbar(
        dbc.Container([
            dbc.Row([
                dbc.Col(html.Span("⚡", style={"fontSize": "1.5rem", "marginRight": "8px"}), width="auto"),
                dbc.Col([
                    html.Span("QuantAgent", style={"fontSize": "1.3rem", "fontWeight": "700", "color": COLORS["text"]}),
                    html.Span(" Dashboard", style={"fontSize": "1.3rem", "fontWeight": "300", "color": COLORS["text_muted"]}),
                ], width="auto"),
            ], align="center", className="g-2"),
            dbc.Row([
                dbc.Col([
                    dbc.Badge("PAPER", id="mode-badge", color="warning", className="me-2"),
                    dbc.Badge("IDLE", id="state-badge", color="secondary"),
                    html.Span(id="clock-display", className="ms-3", style={"fontSize": "0.85rem", "color": COLORS["text_muted"]}),
                ], width="auto"),
            ], align="center"),
        ], fluid=True),
        color=COLORS["bg"],
        dark=True,
        style={"borderBottom": f"1px solid {COLORS['border']}"},
    )


def _build_control_bar() -> dbc.Row:
    return dbc.Row([
        dbc.Col([
            dbc.ButtonGroup([
                dbc.Button("▶ Start", id="btn-start", color="success", size="sm"),
                dbc.Button("⏸ Pause", id="btn-warning", color="warning", size="sm", disabled=True),
                dbc.Button("⏹ Stop", id="btn-stop", color="danger", size="sm", disabled=True),
                dbc.Button("🛑 Emergency", id="btn-emergency", color="danger", size="sm", outline=True),
            ]),
            html.Span(id="control-feedback", className="ms-2", style={"fontSize": "0.8rem"}),
        ], width=4),
        dbc.Col([
            dcc.Dropdown(
                id="token-selector",
                options=[
                    {"label": "⚡ SOL", "value": "SOL"},
                    {"label": "🟡 BNB", "value": "BNB"},
                    {"label": "🥞 CAKE", "value": "CAKE"},
                    {"label": "🐕 BONK", "value": "BONK"},
                    {"label": "🪐 JUP", "value": "JUP"},
                ],
                value="SOL",
                clearable=False,
                style={"width": "200px"},
            ),
        ], width=4, className="d-flex justify-content-center"),
        dbc.Col([
            dcc.Interval(id="update-interval", interval=5000, n_intervals=0),
            html.Span("Auto-refresh: 5s", style={"fontSize": "0.8rem", "color": COLORS["text_muted"]}),
        ], width=4, className="text-end"),
    ], className="mb-3")


def _build_kpi_row() -> dbc.Row:
    return dbc.Row([
        dbc.Col(_stat_card("Portfolio", "kpi-portfolio", "kpi-portfolio-sub", "💰"), width=2),
        dbc.Col(_stat_card("Total P&L", "kpi-pnl", "kpi-pnl-sub", "📈"), width=2),
        dbc.Col(_stat_card("Win Rate", "kpi-winrate", "kpi-winrate-sub", "🎯"), width=2),
        dbc.Col(_stat_card("Total Trades", "kpi-trades", "kpi-trades-sub", "🔄"), width=2),
        dbc.Col(_stat_card("Active Signals", "kpi-signals", "kpi-signals-sub", "📡"), width=2),
        dbc.Col(_stat_card("Risk Score", "kpi-risk", "kpi-risk-sub", "⚠️"), width=2),
    ], className="mb-3")


def _build_kline_panel() -> dbc.Card:
    """K-line chart with signal annotations."""
    return dbc.Card([
        dbc.CardHeader(html.H5("📈 K-Line & Signals", className="mb-0")),
        dbc.CardBody([
            dcc.Graph(id="kline-chart", style={"height": "450px"}),
        ]),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['border']}", "borderRadius": "12px"})


def _build_signal_panel() -> dbc.Card:
    return dbc.Card([
        dbc.CardHeader(html.H5("📡 Signal Monitor", className="mb-0")),
        dbc.CardBody([
            dcc.Graph(id="signal-chart", style={"height": "300px"}),
            html.Hr(style={"borderColor": COLORS["border"]}),
            html.Div(id="signal-list", style={"maxHeight": "200px", "overflowY": "auto"}),
        ]),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['border']}", "borderRadius": "12px"})


def _build_trade_panel() -> dbc.Card:
    return dbc.Card([
        dbc.CardHeader(html.H5("💹 Trade History & P&L", className="mb-0")),
        dbc.CardBody([
            dcc.Graph(id="pnl-chart", style={"height": "250px"}),
            html.Hr(style={"borderColor": COLORS["border"]}),
            html.Div(id="trade-table-container", style={"maxHeight": "200px", "overflowY": "auto"}),
        ]),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['border']}", "borderRadius": "12px"})


def _build_chain_status() -> dbc.Card:
    return dbc.Card([
        dbc.CardHeader(html.H5("⛓️ Chain Status", className="mb-0")),
        dbc.CardBody([
            dcc.Graph(id="chain-chart", style={"height": "250px"}),
            html.Div(id="chain-health", className="mt-3"),
        ]),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['border']}", "borderRadius": "12px"})


def _build_agent_log() -> dbc.Card:
    return dbc.Card([
        dbc.CardHeader(html.H5("🧠 Agent Decision Log", className="mb-0")),
        dbc.CardBody([
            html.Div(id="agent-log", style={
                "maxHeight": "300px", "overflowY": "auto",
                "fontFamily": "monospace", "fontSize": "0.85rem",
            }),
        ]),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['border']}", "borderRadius": "12px"})


def _build_layout() -> html.Div:
    return html.Div([
        _build_header(),
        dbc.Container([
            _build_control_bar(),
            _build_kpi_row(),
            dbc.Row([
                dbc.Col(_build_kline_panel(), width=8),
                dbc.Col(_build_signal_panel(), width=4),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col(_build_trade_panel(), width=7),
                dbc.Col([
                    _build_chain_status(),
                    html.Div(className="mb-3"),
                    _build_agent_log(),
                ], width=5),
            ], className="mb-3"),
            html.Footer(
                html.Small("QuantAgent v0.1.0 | AI-Powered On-Chain Quant Trading", style={"color": COLORS["text_muted"]}),
                className="text-center py-3",
            ),
        ], fluid=True, style={"padding": "20px"}),
    ], style={"backgroundColor": COLORS["bg"], "minHeight": "100vh"})


# ─── OHLCV Data Fetching ───

async def _fetch_ohlcv_for_chart(token: str, chain: str = "solana"):
    """Fetch OHLCV data for chart — prefers real API, falls back to mock."""
    try:
        from quantagent.data.ohlcv_provider import fetch_ohlcv, generate_mock_ohlcv
        candles = await fetch_ohlcv(token, chain=chain, interval="1h", limit=100)
        if not candles:
            base_prices = {"SOL": 172.0, "BNB": 640.0, "CAKE": 2.15, "BONK": 0.000022, "JUP": 0.95}
            candles = generate_mock_ohlcv(
                token=token, chain=chain,
                base_price=base_prices.get(token, 100.0),
                num_candles=100,
            )
        return candles
    except Exception:
        from quantagent.data.ohlcv_provider import generate_mock_ohlcv
        base_prices = {"SOL": 172.0, "BNB": 640.0, "CAKE": 2.15, "BONK": 0.000022, "JUP": 0.95}
        return generate_mock_ohlcv(token=token, chain=chain, base_price=base_prices.get(token, 100.0), num_candles=100)


def _fetch_ohlcv_sync(token: str, chain: str = "solana"):
    """Synchronous wrapper for OHLCV fetch."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            from quantagent.data.ohlcv_provider import generate_mock_ohlcv
            base_prices = {"SOL": 172.0, "BNB": 640.0, "CAKE": 2.15, "BONK": 0.000022, "JUP": 0.95}
            return generate_mock_ohlcv(token=token, chain=chain, base_price=base_prices.get(token, 100.0), num_candles=100)
        return loop.run_until_complete(_fetch_ohlcv_for_chart(token, chain))
    except Exception:
        from quantagent.data.ohlcv_provider import generate_mock_ohlcv
        base_prices = {"SOL": 172.0, "BNB": 640.0, "CAKE": 2.15, "BONK": 0.000022, "JUP": 0.95}
        return generate_mock_ohlcv(token=token, chain=chain, base_price=base_prices.get(token, 100.0), num_candles=100)


# ─── Callbacks ───

def _register_callbacks(app: dash.Dash, agent_ref, api_base_url: str) -> None:

    # ═══════════════════════════════════════════
    # KPI Cards + Status Badges
    # ═══════════════════════════════════════════

    @app.callback(
        [
            Output("kpi-portfolio", "children"), Output("kpi-portfolio-sub", "children"),
            Output("kpi-pnl", "children"), Output("kpi-pnl-sub", "children"),
            Output("kpi-winrate", "children"), Output("kpi-winrate-sub", "children"),
            Output("kpi-trades", "children"), Output("kpi-trades-sub", "children"),
            Output("kpi-signals", "children"), Output("kpi-signals-sub", "children"),
            Output("kpi-risk", "children"), Output("kpi-risk-sub", "children"),
            Output("mode-badge", "children"), Output("mode-badge", "color"),
            Output("state-badge", "children"), Output("state-badge", "color"),
            Output("clock-display", "children"),
        ],
        Input("update-interval", "n_intervals"),
    )
    def update_kpi(n):
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        status_data = {}
        portfolio_data = {}

        # Try agent_ref first (direct in-memory read)
        if agent_ref is not None:
            try:
                status = agent_ref.status
                status_data = status.model_dump()
                portfolio_data = agent_ref.get_portfolio_summary()
            except Exception:
                pass

        # If no agent_ref data, try API
        if not status_data and api_base_url:
            api_status = _api_get(api_base_url, "/agent/status")
            if api_status:
                status_data = api_status
            api_portfolio = _api_get(api_base_url, "/agent/portfolio")
            if api_portfolio:
                portfolio_data = api_portfolio

        portfolio_val = portfolio_data.get("portfolio_usd", 0)
        total_trades = status_data.get("total_trades", 0)
        pnl = status_data.get("total_pnl_usd", 0)
        win_rate = status_data.get("win_rate", 0)
        state = status_data.get("state", "idle")
        mode = portfolio_data.get("trading_mode", "paper")

        risk_score = 100 if not portfolio_data.get("risk_allowed", True) else 0
        state_colors = {
            "idle": "secondary", "monitoring": "info", "signal_detected": "warning",
            "evaluating": "primary", "executing": "success", "post_trade": "info",
            "error": "danger", "paused": "secondary",
        }

        # Count active signals from memory
        signal_count = 0
        if agent_ref is not None:
            try:
                decisions = agent_ref.memory.get_recent_decisions(limit=10)
                signal_count = len(decisions)
            except Exception:
                pass

        return (
            f"${portfolio_val:,.2f}" if portfolio_val > 0 else "$0.00",
            f"Active positions: {portfolio_data.get('active_orders', 0)}",
            f"{'+' if pnl >= 0 else ''}${pnl:,.2f}",
            f"Win rate: {win_rate:.1%}" if total_trades > 0 else "No trades yet",
            f"{win_rate:.1%}" if total_trades > 0 else "N/A",
            f"of {total_trades} trades",
            str(total_trades),
            f"Mode: {mode}",
            str(signal_count),
            "Technical + On-chain + LLM",
            f"{risk_score}/100", "Safe" if risk_score < 50 else "⚠️ Elevated",
            mode.upper(), "warning" if mode == "paper" else "danger",
            state.upper().replace("_", " "), state_colors.get(state, "secondary"),
            now,
        )

    # ═══════════════════════════════════════════
    # K-Line Chart (with real OHLCV + indicators)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("kline-chart", "figure"),
        [Input("update-interval", "n_intervals"), Input("token-selector", "value")],
    )
    def update_kline_chart(n, token):
        """K-line candlestick chart with technical indicators and signal markers."""
        try:
            chain = "solana" if token in ("SOL", "BONK", "JUP", "WIF") else "bsc"
            candles = _fetch_ohlcv_sync(token, chain)

            if not candles:
                return go.Figure().update_layout(
                    paper_bgcolor=COLORS["card_bg"],
                    plot_bgcolor=COLORS["card_bg"],
                    title="No data available",
                )

            # Extract data
            dates = [c.timestamp for c in candles]
            opens = [c.open for c in candles]
            highs = [c.high for c in candles]
            lows = [c.low for c in candles]
            closes = [c.close for c in candles]
            volumes = [c.volume for c in candles]

            import pandas as pd
            from quantagent.signal.technical import calc_rsi, calc_macd, calc_bollinger_bands

            close_series = pd.Series(closes)

            # Calculate indicators
            rsi = calc_rsi(close_series)
            macd_line, signal_line, hist = calc_macd(close_series)
            bb_upper, bb_mid, bb_lower = calc_bollinger_bands(close_series)

            # Create subplots: K-line + Volume | RSI | MACD | BB
            fig = make_subplots(
                rows=4, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                row_heights=[0.5, 0.15, 0.15, 0.2],
                specs=[
                    [{"secondary_y": True}],
                    [{}],  # must be {}, not None
                    [{}],
                    [{}],
                ],
            )

            # K-line
            fig.add_trace(go.Candlestick(
                x=dates, open=opens, high=highs, low=lows, close=closes,
                increasing_line_color=COLORS["bullish"],
                decreasing_line_color=COLORS["bearish"],
                increasing_fillcolor=COLORS["bullish"],
                decreasing_fillcolor=COLORS["bearish"],
                name="K-Line",
            ), row=1, col=1)

            # Volume
            vol_colors = [
                COLORS["volume_up"] if closes[i] >= opens[i] else COLORS["volume_down"]
                for i in range(len(closes))
            ]
            fig.add_trace(go.Bar(
                x=dates, y=volumes,
                marker_color=vol_colors,
                name="Volume",
                opacity=0.5,
                yaxis="y2",
            ), row=1, col=1, secondary_y=True)

            # RSI
            fig.add_trace(go.Scatter(
                x=dates, y=rsi, mode="lines", name="RSI(14)",
                line={"color": COLORS["accent"], "width": 1.5},
            ), row=2, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color=COLORS["bearish"], opacity=0.5, row=2, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color=COLORS["bullish"], opacity=0.5, row=2, col=1)

            # MACD
            fig.add_trace(go.Scatter(
                x=dates, y=macd_line, mode="lines", name="MACD",
                line={"color": COLORS["accent"], "width": 1.5},
            ), row=3, col=1)
            fig.add_trace(go.Scatter(
                x=dates, y=signal_line, mode="lines", name="Signal",
                line={"color": COLORS["neutral"], "width": 1.5},
            ), row=3, col=1)
            hist_colors = [COLORS["bullish"] if v >= 0 else COLORS["bearish"] for v in hist.fillna(0)]
            fig.add_trace(go.Bar(
                x=dates, y=hist.fillna(0), marker_color=hist_colors, name="Histogram", opacity=0.6,
            ), row=3, col=1)

            # Bollinger Bands
            fig.add_trace(go.Scatter(
                x=dates, y=bb_upper, mode="lines", name="BB Upper",
                line={"color": "rgba(136,136,170,0.4)", "width": 1},
            ), row=4, col=1)
            fig.add_trace(go.Scatter(
                x=dates, y=bb_lower, mode="lines", name="BB Lower",
                line={"color": "rgba(136,136,170,0.4)", "width": 1},
                fill="tonexty", fillcolor="rgba(136,136,170,0.08)",
            ), row=4, col=1)
            fig.add_trace(go.Scatter(
                x=dates, y=bb_mid, mode="lines", name="BB Mid",
                line={"color": COLORS["neutral"], "width": 1, "dash": "dot"},
            ), row=4, col=1)
            fig.add_trace(go.Scatter(
                x=dates, y=closes, mode="lines", name="Close",
                line={"color": COLORS["text"], "width": 1.5},
            ), row=4, col=1)

            # Signal annotations on K-line (RSI oversold/overbought)
            for i in range(len(rsi)):
                if pd.notna(rsi.iloc[i]):
                    if rsi.iloc[i] < 30:
                        fig.add_annotation(
                            x=dates[i], y=lows[i],
                            text="📈", showarrow=True, arrowhead=2, arrowcolor=COLORS["bullish"],
                            row=1, col=1,
                        )
                    elif rsi.iloc[i] > 70:
                        fig.add_annotation(
                            x=dates[i], y=highs[i],
                            text="📉", showarrow=True, arrowhead=2, arrowcolor=COLORS["bearish"],
                            row=1, col=1,
                        )

            chain_color = CHAIN_COLORS.get(chain, COLORS["text"])
            fig.update_layout(
                paper_bgcolor=COLORS["card_bg"],
                plot_bgcolor=COLORS["card_bg"],
                font={"color": COLORS["text"]},
                margin={"t": 40, "b": 20, "l": 50, "r": 20},
                height=500,
                title={
                    "text": f"{token} <span style='color:{chain_color}'>({chain.upper()})</span>",
                    "font": {"size": 16},
                },
                showlegend=False,
                xaxis_rangeslider_visible=False,
            )
            fig.update_yaxes(title_text="Price", row=1, col=1, gridcolor=COLORS["border"])
            fig.update_yaxes(title_text="RSI", row=2, col=1, gridcolor=COLORS["border"], range=[0, 100])
            fig.update_yaxes(title_text="MACD", row=3, col=1, gridcolor=COLORS["border"])
            fig.update_yaxes(title_text="BB", row=4, col=1, gridcolor=COLORS["border"])
            fig.update_yaxes(title_text="Vol", row=1, col=1, secondary_y=True, showgrid=False)

            return fig

        except Exception as e:
            # Graceful fallback on any chart error
            fig = go.Figure()
            fig.add_annotation(
                text=f"Chart error: {type(e).__name__}",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font={"size": 14, "color": COLORS["bearish"]},
            )
            fig.update_layout(
                paper_bgcolor=COLORS["card_bg"],
                plot_bgcolor=COLORS["card_bg"],
                font={"color": COLORS["text"]},
            )
            return fig

    # ═══════════════════════════════════════════
    # Signal Gauge Chart (from real agent data)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("signal-chart", "figure"),
        Input("update-interval", "n_intervals"),
    )
    def update_signal_chart(n):
        """Signal gauge chart — reads real signal strengths from agent memory."""
        # Default neutral values
        values = [50, 50, 50]

        if agent_ref is not None:
            try:
                decisions = agent_ref.memory.get_recent_decisions(limit=1)
                if decisions:
                    sig = decisions[0].signal
                    # Extract signal type → strength mapping
                    # bullish > 60, neutral 40-60, bearish < 40
                    sig_type = sig.signal_type.value if hasattr(sig, 'signal_type') else 'neutral'
                    confidence = sig.confidence if hasattr(sig, 'confidence') else 0.5
                    base = 75 if sig_type == 'bullish' else (25 if sig_type == 'bearish' else 50)
                    # Modulate by confidence
                    values = [
                        min(100, max(0, base + (confidence - 0.5) * 30)),  # Technical
                        min(100, max(0, base + (confidence - 0.5) * 20)),  # On-chain
                        min(100, max(0, base + (confidence - 0.5) * 25)),  # LLM
                    ]
            except Exception:
                pass

        fig = make_subplots(
            rows=1, cols=3,
            subplot_titles=("Technical", "On-Chain", "LLM"),
            specs=[[{"type": "indicator"}] * 3],
        )
        for i, (val, name) in enumerate(zip(values, ["Technical", "On-Chain", "LLM"])):
            fig.add_trace(go.Indicator(
                mode="gauge+number+delta",
                value=val,
                domain={"x": [0, 1], "y": [0, 1]},
                title={"text": name, "font": {"size": 14, "color": COLORS["text"]}},
                gauge={
                    "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": COLORS["text_muted"]},
                    "bar": {"color": COLORS["bullish"] if val > 60 else (COLORS["bearish"] if val < 40 else COLORS["neutral"])},
                    "bgcolor": COLORS["card_bg"],
                    "borderwidth": 0,
                    "steps": [
                        {"range": [0, 40], "color": "rgba(255,23,68,0.1)"},
                        {"range": [40, 60], "color": "rgba(255,171,0,0.1)"},
                        {"range": [60, 100], "color": "rgba(0,230,118,0.1)"},
                    ],
                    "threshold": {"line": {"color": "white", "width": 2}, "thickness": 0.75, "value": 50},
                },
                number={"font": {"size": 24, "color": COLORS["text"]}},
                delta={"reference": 50, "font": {"size": 14}},
            ), row=1, col=i + 1)

        fig.update_layout(
            paper_bgcolor=COLORS["card_bg"], plot_bgcolor=COLORS["card_bg"],
            font={"color": COLORS["text"]}, margin={"t": 50, "b": 20, "l": 20, "r": 20}, height=300,
        )
        return fig

    # ═══════════════════════════════════════════
    # P&L Chart (from real trade history)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("pnl-chart", "figure"),
        Input("update-interval", "n_intervals"),
    )
    def update_pnl_chart(n):
        """P&L cumulative curve — from real trade records."""
        fig = go.Figure()

        trades = _get_agent_trades(agent_ref, limit=100)

        if trades:
            # Sort by timestamp and compute cumulative P&L
            sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", ""))
            cumulative_pnl = 0.0
            x_vals = []
            y_vals = []

            for i, t in enumerate(sorted_trades):
                pnl = t.get("pnl_usd", 0.0)
                cumulative_pnl += pnl
                ts = t.get("timestamp", "")
                # Format timestamp for display
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    label = dt.strftime("%m/%d %H:%M")
                except Exception:
                    label = f"#{i+1}"
                x_vals.append(label)
                y_vals.append(round(cumulative_pnl, 2))

            if x_vals:
                fill_color = "rgba(0,230,118,0.1)" if y_vals[-1] >= 0 else "rgba(255,23,68,0.1)"
                line_color = COLORS["bullish"] if y_vals[-1] >= 0 else COLORS["bearish"]

                fig.add_trace(go.Scatter(
                    x=x_vals, y=y_vals,
                    mode="lines+markers", name="Cumulative P&L",
                    line={"color": line_color, "width": 2},
                    fill="tozeroy", fillcolor=fill_color,
                    marker={"size": 4},
                ))
        else:
            # No trades yet — show empty state
            fig.add_annotation(
                text="No trades recorded yet",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font={"size": 16, "color": COLORS["text_muted"]},
            )

        fig.add_hline(y=0, line_dash="dash", line_color=COLORS["text_muted"], opacity=0.5)

        fig.update_layout(
            paper_bgcolor=COLORS["card_bg"], plot_bgcolor=COLORS["card_bg"],
            font={"color": COLORS["text"]}, margin={"t": 30, "b": 30, "l": 50, "r": 20}, height=250,
            xaxis={"title": "Trade Time", "gridcolor": COLORS["border"]},
            yaxis={"title": "P&L (USD)", "gridcolor": COLORS["border"], "tickprefix": "$"},
            showlegend=False,
        )
        return fig

    # ═══════════════════════════════════════════
    # Chain Price Chart (live prices from API)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("chain-chart", "figure"),
        Input("update-interval", "n_intervals"),
    )
    def update_chain_chart(n):
        """Chain price display — live prices from API or agent."""
        fig = go.Figure()

        # Try to get live prices
        sol_price = None
        bnb_price = None
        sol_ref = 175.20
        bnb_ref = 638.80

        prices = _get_live_prices(api_base_url, ["SOL", "BNB"])
        if prices:
            sol_price = prices.get("SOL", {}).get("usd") if isinstance(prices.get("SOL"), dict) else prices.get("SOL")
            bnb_price = prices.get("BNB", {}).get("usd") if isinstance(prices.get("BNB"), dict) else prices.get("BNB")

        # Fallback: try agent_ref directly
        if sol_price is None and agent_ref is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    sol_price = loop.run_until_complete(agent_ref.dex_collector.get_price("SOL", "solana"))
                    bnb_price = loop.run_until_complete(agent_ref.dex_collector.get_price("BNB", "bsc"))
            except Exception:
                pass

        # Build indicator chart
        sol_display = sol_price if sol_price else 0
        bnb_display = bnb_price if bnb_price else 0

        fig.add_trace(go.Indicator(
            mode="number+delta",
            value=sol_display,
            title={"text": "SOL", "font": {"color": COLORS["solana"], "size": 16}},
            number={"font": {"size": 28, "color": COLORS["solana"]}, "prefix": "$", "valueformat": ".2f"},
            delta={"reference": sol_ref, "relative": True, "valueformat": ".2%"} if sol_display else {},
            domain={"row": 0, "column": 0},
        ))
        fig.add_trace(go.Indicator(
            mode="number+delta",
            value=bnb_display,
            title={"text": "BNB", "font": {"color": COLORS["bsc"], "size": 16}},
            number={"font": {"size": 28, "color": COLORS["bsc"]}, "prefix": "$", "valueformat": ".2f"},
            delta={"reference": bnb_ref, "relative": True, "valueformat": ".2%"} if bnb_display else {},
            domain={"row": 1, "column": 0},
        ))
        fig.update_layout(
            grid={"rows": 2, "columns": 1, "pattern": "independent"},
            paper_bgcolor=COLORS["card_bg"], font={"color": COLORS["text"]},
            margin={"t": 30, "b": 20, "l": 20, "r": 20}, height=250,
        )
        return fig

    # ═══════════════════════════════════════════
    # Agent Decision Log (from real memory)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("agent-log", "children"),
        Input("update-interval", "n_intervals"),
    )
    def update_agent_log(n):
        """Agent decision log — from real agent memory or API."""
        log_entries = []

        # Try agent memory (real decisions)
        if agent_ref is not None:
            try:
                decisions = agent_ref.memory.get_recent_decisions(limit=15)
                for d in reversed(decisions):  # Newest last
                    ts = d.timestamp.strftime("%H:%M:%S") if hasattr(d.timestamp, 'strftime') else str(d.timestamp)[:8]
                    sig = d.signal
                    action = d.action_taken
                    sig_type = sig.signal_type.value if hasattr(sig, 'signal_type') else 'neutral'
                    sig_conf = sig.confidence if hasattr(sig, 'confidence') else 0
                    action_name = action.action.value if hasattr(action, 'action') else str(action)
                    token = sig.token if hasattr(sig, 'token') else '?'
                    chain = sig.chain.value if hasattr(sig, 'chain') else '?'

                    level = "INFO"
                    msg = f"[{token}/{chain}] {sig_type.upper()} conf={sig_conf:.2f} → {action_name.upper()}"
                    if hasattr(action, 'reasoning') and action.reasoning:
                        msg += f" | {action.reasoning[:60]}"

                    log_entries.append({"time": ts, "level": level, "msg": msg})
            except Exception:
                pass

        # Try API as fallback
        if not log_entries:
            api_data = _api_get(api_base_url, "/agent/memory?limit=15")
            if api_data and "decisions" in api_data:
                for d in reversed(api_data["decisions"]):
                    ts = d.get("timestamp", "")[-8:]
                    level = "INFO"
                    msg = f"Decision {d.get('decision_id', '?')}"
                    log_entries.append({"time": ts, "level": level, "msg": msg})

        # If still empty, show waiting message
        if not log_entries:
            log_entries = [
                {"time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "level": "DEBUG",
                 "msg": "Agent idle — waiting for monitoring cycle to start"},
            ]

        level_colors = {
            "DEBUG": COLORS["text_muted"], "INFO": COLORS["accent"],
            "WARNING": COLORS["neutral"], "ERROR": COLORS["bearish"],
        }
        items = []
        for entry in log_entries:
            color = level_colors.get(entry["level"], COLORS["text"])
            items.append(html.Div([
                html.Span(entry["time"], style={"color": COLORS["text_muted"], "marginRight": "8px"}),
                html.Span(f"[{entry['level']}]", style={"color": color, "marginRight": "8px", "fontWeight": "600"}),
                html.Span(entry["msg"]),
            ], style={"padding": "3px 0", "borderBottom": f"1px solid {COLORS['border']}"}))
        return items

    # ═══════════════════════════════════════════
    # Signal List (from real agent decisions)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("signal-list", "children"),
        Input("update-interval", "n_intervals"),
    )
    def update_signal_list(n):
        """Signal list — from real agent signal data."""
        signals = []

        if agent_ref is not None:
            try:
                decisions = agent_ref.memory.get_recent_decisions(limit=5)
                for d in reversed(decisions):
                    sig = d.signal
                    sig_type = sig.signal_type.value if hasattr(sig, 'signal_type') else 'neutral'
                    confidence = sig.confidence if hasattr(sig, 'confidence') else 0
                    token = sig.token if hasattr(sig, 'token') else '?'
                    chain = sig.chain.value if hasattr(sig, 'chain') else 'solana'
                    # Determine source from signal attributes
                    source = "combined"
                    indicator = "SignalCombiner"
                    desc = sig.description if hasattr(sig, 'description') else f"Confidence: {confidence:.2f}"

                    signals.append({
                        "token": token, "chain": chain, "source": source,
                        "type": sig_type, "indicator": indicator,
                        "confidence": confidence, "desc": desc,
                    })
            except Exception:
                pass

        # If no signals, show empty state
        if not signals:
            return html.Div(
                html.Small("No signals detected yet", style={"color": COLORS["text_muted"]}),
                className="text-center py-3",
            )

        items = []
        for sig in signals:
            type_color = SIGNAL_COLORS.get(sig["type"], COLORS["neutral"])
            chain_color = CHAIN_COLORS.get(sig["chain"], COLORS["text_muted"])
            items.append(html.Div([
                html.Span(sig["token"], style={"fontWeight": "700", "marginRight": "6px"}),
                html.Span(f"({sig['chain']})", style={"color": chain_color, "fontSize": "0.8rem", "marginRight": "8px"}),
                html.Span(f"[{sig['source']}]", style={"color": COLORS["text_muted"], "fontSize": "0.8rem", "marginRight": "8px"}),
                html.Span(sig["type"].upper(), style={
                    "color": type_color, "fontWeight": "600", "fontSize": "0.8rem",
                    "backgroundColor": f"{type_color}22", "padding": "2px 8px", "borderRadius": "4px",
                }),
                html.Span(f" conf:{sig['confidence']:.0%}", style={"fontSize": "0.8rem", "color": COLORS["text_muted"]}),
                html.Br(),
                html.Small(sig["desc"], style={"color": COLORS["text_muted"]}),
            ], style={
                "padding": "8px", "marginBottom": "6px",
                "borderLeft": f"3px solid {type_color}",
                "backgroundColor": f"{type_color}08", "borderRadius": "4px",
            }))
        return items

    # ═══════════════════════════════════════════
    # Trade Table (from real trade history)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("trade-table-container", "children"),
        Input("update-interval", "n_intervals"),
    )
    def update_trade_table(n):
        """Trade table — from real trade records."""
        trades = _get_agent_trades(agent_ref, limit=20)

        if not trades:
            return html.Div(
                html.Small("No trades recorded yet", style={"color": COLORS["text_muted"]}),
                className="text-center py-3",
            )

        rows = []
        for t in reversed(trades):  # Newest first
            # Format timestamp
            ts = t.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M:%S")
            except Exception:
                time_str = ts[-8:] if len(ts) > 8 else ts

            action = t.get("action", "hold").upper()
            token = t.get("token", "?")
            chain = t.get("chain", "solana")
            amount = t.get("amount_in", 0)
            pnl = t.get("pnl_usd", 0)
            mode = t.get("mode", "paper")

            action_color = COLORS["bullish"] if action == "BUY" else COLORS["bearish"]
            pnl_str = f"{'+' if pnl > 0 else ''}${pnl:.2f}" if pnl != 0 else "--"
            pnl_color = COLORS["bullish"] if pnl > 0 else (COLORS["bearish"] if pnl < 0 else COLORS["text_muted"])
            amount_str = f"${amount:.2f}" if amount > 0 else "--"

            rows.append(html.Div([
                html.Span(time_str, style={"color": COLORS["text_muted"], "marginRight": "10px", "fontFamily": "monospace"}),
                html.Span(action, style={"color": action_color, "fontWeight": "700", "marginRight": "10px"}),
                html.Span(token, style={"marginRight": "6px", "fontWeight": "600"}),
                html.Span(f"({chain})", style={"color": CHAIN_COLORS.get(chain, COLORS["text_muted"]), "fontSize": "0.8rem", "marginRight": "10px"}),
                html.Span(amount_str, style={"marginRight": "10px"}),
                html.Span(pnl_str, style={"color": pnl_color, "fontWeight": "600"}),
                html.Span(f" [{mode.upper()}]", style={"color": COLORS["text_muted"], "fontSize": "0.75rem", "marginLeft": "6px"}),
            ], style={"padding": "6px 0", "borderBottom": f"1px solid {COLORS['border']}"}))
        return rows

    # ═══════════════════════════════════════════
    # Chain Health Status (from API health check)
    # ═══════════════════════════════════════════

    @app.callback(
        Output("chain-health", "children"),
        Input("update-interval", "n_intervals"),
    )
    def update_chain_health(n):
        """Chain health — from API /health endpoint."""
        health_data = _api_get(api_base_url, "/health")
        items = []

        if health_data and "chains" in health_data:
            chain_map = {"solana": "Solana", "bsc": "BSC"}
            for chain_key, healthy in health_data["chains"].items():
                name = chain_map.get(chain_key, chain_key)
                items.append(_chain_health_item(name, chain_key, bool(healthy), "--"))
        else:
            # Fallback: show both as unknown
            items.append(_chain_health_item("Solana", "solana", None, "N/A"))
            items.append(_chain_health_item("BSC", "bsc", None, "N/A"))

        return html.Div(items)

    def _chain_health_item(name, chain, healthy, latency):
        color = CHAIN_COLORS.get(chain, COLORS["text"])
        if healthy is True:
            icon = "🟢"
        elif healthy is False:
            icon = "🔴"
        else:
            icon = "⚪"
        latency_text = f"Latency: {latency}" if latency != "N/A" else "Status: Checking..."
        return html.Div([
            html.Span(f"{icon} {name}", style={"fontWeight": "600", "color": color, "marginRight": "10px"}),
            html.Span(latency_text, style={"color": COLORS["text_muted"], "fontSize": "0.85rem"}),
        ], style={"padding": "6px 0"})

    # ═══════════════════════════════════════════
    # Agent Control Buttons
    # ═══════════════════════════════════════════

    @app.callback(
        [
            Output("control-feedback", "children"),
            Output("btn-start", "disabled"),
            Output("btn-stop", "disabled"),
            Output("btn-warning", "disabled"),
        ],
        [
            Input("btn-start", "n_clicks"),
            Input("btn-stop", "n_clicks"),
            Input("btn-emergency", "n_clicks"),
        ],
        prevent_initial_call=True,
    )
    def handle_control(start_clicks, stop_clicks, emergency_clicks):
        """Handle agent start/stop/emergency control buttons."""
        triggered = ctx.triggered_id if ctx.triggered_id else ""

        if triggered == "btn-start":
            # Try API first
            result = _api_post(api_base_url, "/agent/start?poll_interval=60")
            if result:
                return f"✅ Agent started (API)", True, False, False

            # Try agent_ref directly
            if agent_ref is not None:
                try:
                    # Start agent in background thread
                    import threading
                    def _run_agent():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(agent_ref.run(poll_interval=60))
                    t = threading.Thread(target=_run_agent, daemon=True)
                    t.start()
                    return "✅ Agent started (direct)", True, False, False
                except Exception as e:
                    return f"❌ Failed: {str(e)[:50]}", False, True, True

            return "❌ No agent available", False, True, True

        elif triggered == "btn-stop":
            result = _api_post(api_base_url, "/agent/stop")
            if result:
                return "⏹ Agent stopping (API)", False, True, True

            if agent_ref is not None:
                try:
                    agent_ref.stop()
                    return "⏹ Agent stopping (direct)", False, True, True
                except Exception as e:
                    return f"❌ Failed: {str(e)[:50]}", False, True, True

            return "❌ No agent available", False, True, True

        elif triggered == "btn-emergency":
            result = _api_post(api_base_url, "/agent/emergency-stop")
            if result:
                return "🛑 EMERGENCY STOP (API)", False, True, True

            if agent_ref is not None:
                try:
                    agent_ref.risk_manager.set_emergency_stop(True)
                    agent_ref.stop()
                    return "🛑 EMERGENCY STOP (direct)", False, True, True
                except Exception as e:
                    return f"❌ Failed: {str(e)[:50]}", False, True, True

            return "❌ No agent available", False, True, True

        return "", False, True, True


def run_dashboard(host: str = "0.0.0.0", port: int = 8050, debug: bool = True):
    """Run the dashboard server."""
    app = create_dashboard()
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard()
