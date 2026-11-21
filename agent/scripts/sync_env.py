"""从 backend 的配置同步生成 agent/.env。

为什么需要这个脚本：
Agent 服务要直连业务库，而数据库密码在 `backend/configs/config.yaml` 里。
两份配置各写一次密码，早晚会漂移（改了后端忘了改 Agent，然后所有工具报
"Access denied"）。所以让 Agent 的 `.env` **从后端配置派生出**，只维护一处。

用法：
    cd agent
    python scripts/sync_env.py

已经存在的 .env 会被覆盖 —— 这个文件本来就是生成物。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# agent/scripts/sync_env.py → agent/ → 仓库根
AGENT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_DIR.parent
BACKEND_CONFIG = REPO_ROOT / "backend" / "configs" / "config.yaml"
ENV_PATH = AGENT_DIR / ".env"

REQUIRED_MYSQL_KEYS = ("host", "port", "user", "password", "database")


def parse_mysql_section(text: str) -> dict[str, str]:
    """只取 `mysql:` 这一段下的键值。

    为什么要按段解析：yaml 里 `port` 同时出现在 `server:` 和 `mysql:` 下，
    直接正则抓 "port" 会抓到 HTTP 端口，然后 Agent 会去连 8080 上的 MySQL。
    """
    section: str | None = None
    values: dict[str, str] = {}

    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue

        # 顶层键：形如 "mysql:"
        if not line[0].isspace() and line.rstrip().endswith(":"):
            section = line.strip().rstrip(":")
            continue

        if section != "mysql":
            continue

        match = re.match(r"\s+([a-z_]+):\s*\"?([^\"#\r\n]*)\"?", line)
        if match:
            values[match.group(1)] = match.group(2).strip()

    return values


def main() -> int:
    if not BACKEND_CONFIG.is_file():
        print(f"找不到后端配置：{BACKEND_CONFIG}", file=sys.stderr)
        return 1

    mysql = parse_mysql_section(BACKEND_CONFIG.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_MYSQL_KEYS if key not in mysql]
    if missing:
        print(f"backend 配置的 mysql 段缺少字段：{missing}", file=sys.stderr)
        return 1

    content = f"""# 本机配置。由 scripts/sync_env.py 从 backend/configs/config.yaml 生成。
# 不要手动改这个文件 —— 改了后端密码后重新跑一次脚本。
# .gitignore 已覆盖 .env，不会进仓库。

AGENT_HOST=127.0.0.1
AGENT_PORT=8090
AGENT_INTERNAL_TOKEN=dev-internal-token-change-me
AGENT_GO_BASE_URL=http://127.0.0.1:8080

AGENT_MYSQL_HOST={mysql['host']}
AGENT_MYSQL_PORT={mysql['port']}
AGENT_MYSQL_USER={mysql['user']}
AGENT_MYSQL_PASSWORD={mysql['password']}
AGENT_MYSQL_DATABASE={mysql['database']}
"""
    # 不带 BOM：BOM 会污染第一行的键名
    ENV_PATH.write_text(content, encoding="utf-8", newline="\n")

    print(f"已写入 {ENV_PATH}")
    print(
        f"  mysql {mysql['host']}:{mysql['port']}/{mysql['database']} "
        f"user={mysql['user']} password={len(mysql['password'])} 位"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
