"""API 层：只做 HTTP 适配，不含业务逻辑。"""

from .routes import router

__all__ = ["router"]
