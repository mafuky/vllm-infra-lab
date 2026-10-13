import sys
from pathlib import Path

# 让测试以仓库根目录为起点导入 scripts 包，并让相对路径（fixtures、lessons）可用
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
