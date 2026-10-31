"""路由包。

各子模块用 ``APIRouter`` 定义接口，在 ``backend.main`` 里统一 ``include_router``。
这里保持轻量，避免在包初始化时引入 FastAPI 依赖导致循环导入。
"""
