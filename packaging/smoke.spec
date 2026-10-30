# Reuse the application dependencies/resources with a separate CI-only entry point.
from pathlib import Path

_NRCLIP_SMOKE_BUILD = True
shared_spec = Path(SPECPATH) / 'nrclip_builder.spec'
exec(compile(shared_spec.read_text(encoding='utf-8'), str(shared_spec), 'exec'))
