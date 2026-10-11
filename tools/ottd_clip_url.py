"""Add an uploaded clip's asset url to its metadata file: python tools/ottd_clip_url.py PAYLOAD SEQ URL"""
import json
import sys
from pathlib import Path

p = Path(sys.argv[1]) / f"seg_{sys.argv[2]}.json"
d = json.loads(p.read_text(encoding="utf-8"))
d["url"] = sys.argv[3]
p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
print(d["label"], d["game_from"], d["game_to"], d["bytes"])
