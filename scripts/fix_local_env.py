"""
fix_local_env.py — 把 backend/.env.local.example 复制为 backend/.env

用法:
    python scripts/fix_local_env.py   # 从项目根目录运行
    python fix_local_env.py           # 从 scripts/ 目录运行
"""
import os
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
EXAMPLE = os.path.join(ROOT_DIR, "backend", ".env.local.example")
TARGET = os.path.join(ROOT_DIR, "backend", ".env")

if not os.path.exists(EXAMPLE):
    print(f"❌ 找不到模板文件：{EXAMPLE}")
    raise SystemExit(1)

if os.path.exists(TARGET):
    print(f"⚠️  backend/.env 已存在，是否覆盖？(y/N) ", end="", flush=True)
    if input().strip().lower() != "y":
        print("已取消，.env 未修改。")
        raise SystemExit(0)

shutil.copy2(EXAMPLE, TARGET)
print(f"✅ 已生成 backend/.env")
print()
print("还需要一步：用文本编辑器打开下面这个文件，把 FAL_KEY= 后面填入真实值")
print(f"   {TARGET}")
print()
print("其余配置（JWT_SECRET、ALLOWED_ORIGINS、DATABASE_PATH 等）本地开发用默认值即可。")
