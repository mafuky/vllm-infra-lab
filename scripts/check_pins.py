"""镜像版本锁定检查。

── 为什么要有它 ──────────────────────────────────────────────

`latest`、`main-stable` 这类标签会随上游发布悄悄变化。今天压出来的数字，
下周重建 Pod 可能就换了一个版本，结果没法复现，也说不清当时用的是哪个版本。
所以仓库里的每个镜像都必须写死具体版本号，或者用 @sha256 摘要。

── 怎么执行 ──────────────────────────────────────────────────

    python3 scripts/check_pins.py lessons/**/*.yaml

有未锁定的镜像就逐个打印，并以退出码 1 结束；`make check` 会调用它。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_IMAGE_LINE = re.compile(r"^\s*-?\s*image:\s*[\"']?([^\"'\s#]+)")


def _is_floating(ref: str) -> bool:
    if "@sha256:" in ref:
        return False
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        # 不写标签等于 latest；registry 的端口号（localhost:5000/foo）在最后一个 / 之前，不算标签
        return True
    tag = last.split(":", 1)[1]
    if tag == "latest":
        return True
    # main-stable 这类没有数字版本的「稳定」标签同样会漂移
    return "stable" in tag and not re.search(r"\d", tag)


def find_floating_images(yaml_text: str) -> list[str]:
    """返回 yaml_text 中所有未锁定版本的镜像引用，保持出现顺序。"""
    found = []
    for line in yaml_text.splitlines():
        m = _IMAGE_LINE.match(line)
        if m and _is_floating(m.group(1)):
            found.append(m.group(1))
    return found


def main(paths: list[str]) -> int:
    bad = 0
    for path in paths:
        for ref in find_floating_images(Path(path).read_text(encoding="utf-8")):
            print(f"{path}: {ref} 没有锁定版本")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
