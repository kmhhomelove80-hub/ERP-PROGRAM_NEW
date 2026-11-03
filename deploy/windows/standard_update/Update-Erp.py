"""SUNIL ERP standard update entrypoint. No EXE or execution-policy changes."""
from release_updater import main
if __name__ == '__main__':
    raise SystemExit(main())
