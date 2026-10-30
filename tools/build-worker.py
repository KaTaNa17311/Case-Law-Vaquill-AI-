from pathlib import Path
import json
root=Path(__file__).resolve().parents[1]
(root/'worker/index.js').write_text('const page = '+json.dumps((root/'frontend/index.html').read_text(),ensure_ascii=False)+';\n'+(root/'tools/gateway.js').read_text())
