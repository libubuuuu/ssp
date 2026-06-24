---
name: feedback-ssp-venv-shebang-fix
description: green slot venv scripts have wrong shebangs after rsync from /root; fix with Python not sed -i
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ea970383-b924-47d6-82d5-cd1a1bbb168e
---

When deploying to a fresh green/blue slot, `venv/bin/` scripts retain shebang `#!/root/ssp/backend/venv/bin/python3`. `ssp-app` user can't enter `/root/` (drwx------) → `spawn error` / "bad interpreter: Permission denied".

**Why:** venv scripts embed absolute path of the venv at creation time. `/opt/ssp-*/backend/venv/` was originally created while sourced from `/root/ssp/backend/venv/`, so shebangs baked in the root path.

**How to apply:** Before or after deploy.sh when a slot shows `spawn error`, run:
```python
/usr/bin/python3 -c "
import os
bin_dir = '/opt/ssp-green/backend/venv/bin'  # change to blue/green as needed
old = b'#!/root/ssp/backend/venv/bin/python3'
new = b'#!/opt/ssp-green/backend/venv/bin/python3'
for name in os.listdir(bin_dir):
    path = os.path.join(bin_dir, name)
    if not os.path.isfile(path) or os.path.islink(path): continue
    data = open(path,'rb').read()
    if old in data:
        open(path,'wb').write(data.replace(old,new))
        print('Fixed:', name)
"
```
Do NOT use sed -i (file-clearing bug risk, per [[feedback-ssp-no-sed-batch]]).
