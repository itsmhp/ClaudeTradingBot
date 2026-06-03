"""Add /scan endpoint to routes.py"""
import pathlib

path = pathlib.Path(r"c:\Users\muham\OneDrive\Documents\Anggaran INF\Codes\ClaudeTradingBot\api\routes.py")
content = path.read_text(encoding="utf-8")

scan_code = '''

class ScanRequest(BaseModel):
    symbol: str = "XAUUSD"
    strategy: str = "SWING"  # SWING, SCALPING, or AUTO


@router.post("/scan")
async def manual_scan(req: ScanRequest):
    """Trigger a manual AI chart scan for a specific pair via Claude."""
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        signal = await engine.process_pair(req.symbol, req.strategy)
        if signal:
            return {
                "status": "signal_found",
                "signal": signal.model_dump() if hasattr(signal, "model_dump") else vars(signal),
            }
        return {"status": "no_signal", "pair": req.symbol, "strategy": req.strategy}
    except Exception as exc:
        logger.error(f"[scan] Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

'''

content = content.replace('@router.post("/pause")', scan_code + '@router.post("/pause")', 1)
path.write_text(content, encoding="utf-8")
print("Done.")
print("/scan present:", "/scan" in content)
