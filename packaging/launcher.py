import sys

try:
    from nrclip_builder.app import main
    result = main()
except Exception:
    if "--smoke-test" in sys.argv:
        # Windowed bootloaders otherwise show a blocking traceback dialog in CI.
        raise SystemExit(1)
    raise
raise SystemExit(result)
