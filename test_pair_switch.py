"""Test pair switching on Binance Desktop."""
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.event_bus import EventBus
from automation.binance_app import BinanceApp
from automation.pair_switcher import PairSwitcher

app = BinanceApp()
print("Connecting to Binance Desktop...")
print("(Binance should be visible and NOT minimized!)")
if not app.connect():
    print("FAILED!")
    sys.exit(1)

n = len(app._descendants)
print(f"Connected! {n} elements")
if n < 100:
    print("WARNING: Too few elements. Binance may be minimized.")
    print("Please bring Binance Desktop to foreground and re-run.")
    sys.exit(1)

event_bus = EventBus()
switcher = PairSwitcher(app, event_bus)

# Get current pair
current = switcher.get_current_pair()
print(f"Current pair: {current}")

# Try switching to ETHUSDT (very common, always in futures)
target = "ETHUSDT"
print(f"\nSwitching to {target}...")
success = switcher.switch_to(target)
print(f"Result: {'SUCCESS' if success else 'FAILED'}")

if success:
    time.sleep(1)
    new_pair = switcher.get_current_pair()
    print(f"New pair: {new_pair}")

    # Switch back
    if current:
        print(f"\nSwitching back to {current}...")
        success2 = switcher.switch_to(current)
        print(f"Result: {'SUCCESS' if success2 else 'FAILED'}")

print("\nDone!")
