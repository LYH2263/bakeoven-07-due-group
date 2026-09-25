import os
import tempfile

# 必须在导入 app 任何模块之前生效：config 在 import 时读取环境变量。
_fd, _db_path = tempfile.mkstemp(prefix="bakeoven-lifespan-", suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["SEED_ON_EMPTY"] = "false"
