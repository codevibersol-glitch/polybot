"""
main.py
───────
PolyBot – Polymarket Automated Desktop Trading Bot
Entry point.

Usage:
    python main.py

PyInstaller (single .exe):
    pip install pyinstaller
    pyinstaller --onefile --windowed --name PolyBot main.py
"""

import sys
import traceback
from pathlib import Path

# ── Ensure project root is on PYTHONPATH so sub-packages resolve correctly ────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Pre-flight dependency check (friendly error message on missing deps) ───────
_REQUIRED = {
    "customtkinter":  "pip install customtkinter",
    "py_clob_client": "pip install py-clob-client",
    "requests":       "pip install requests",
    "websocket":      "pip install websocket-client",
    "pandas":         "pip install pandas",
    "dateutil":       "pip install python-dateutil",
}

_missing = []
for pkg, install_cmd in _REQUIRED.items():
    try:
        __import__(pkg)
    except ImportError:
        _missing.append((pkg, install_cmd))

if _missing:
    print("=" * 60)
    print("PolyBot is missing required packages.  Please run:")
    print()
    for pkg, cmd in _missing:
        print(f"  {cmd}")
    print()
    print("Or install everything at once:")
    print("  pip install -r requirements.txt")
    print("=" * 60)
    sys.exit(1)

# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    """Launch the PolyBot GUI application."""
    import customtkinter as ctk
    from gui.app import PolybotApp
    from utils.logger import get_logger

    log = get_logger("main")
    log.info("PolyBot starting up – Python %s", sys.version.split()[0])

    try:
        app = PolybotApp()
        app.mainloop()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt – shutting down.")
    except Exception:
        # Show a last-resort error dialog if the GUI itself crashes
        err = traceback.format_exc()
        log.critical("Unhandled exception:\n%s", err)
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            from tkinter import messagebox
            messagebox.showerror(
                "Fatal Error",
                f"PolyBot encountered an unexpected error:\n\n{err}\n\n"
                "Check polybot.log for details.",
            )
            root.destroy()
        except Exception:
            print(err)
        sys.exit(1)


if __name__ == "__main__":
    main()
