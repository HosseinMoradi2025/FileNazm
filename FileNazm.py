"""FileNazm 1.1.0 — CPython 3.8.10 / Windows 7 SP1 compatibility target."""
import sys
sys.dont_write_bytecode = True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="فایل‌نظم ۱.۱.۰")
    parser.add_argument("--demo", action="store_true", help="دادهٔ خیالی، بدون ذخیره یا عملیات واقعی")
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    args = parser.parse_args()
    from ui import App
    App(demo=args.demo, autostart=args.autostart, tray=not args.no_tray).run()


if __name__ == "__main__":
    main()
