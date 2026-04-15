"""
统一 API 响应格式
"""
from pydantic import BaseModel
from typing import Optional, Any, Dict


class APIResponse(BaseModel):
    """统一 API 响应模型"""
    success: bool = True
    data: Optional[Any] = None
    error: Optional[str] = None
    cost: Optional[int] = None


class TaskResponse(BaseModel):
    """任务提交响应"""
    success: bool = True
    task_id: str
    status: str = "pending"
    message: Optional[str] = None
    cost: Optional[int] = None
    error: Optional[str] = None


def success_response(data: Any = None, cost: int = None, **extra) -> dict:
    """成功响应"""
    resp = {
        "success": True,
        "data": data,
        "cost": cost,
    }
    resp.update(extra)
    return resp


def error_response(error: str, status_code: int = 400) -> dict:
    """错误响应"""
    return {
        "success": False,
        "error": error,
    }


def task_response(task_id: str, status: str = "pending", message: str = None, cost: int = None) -> dict:
    """任务响应"""
    return {
        "success": True,
        "task_id": task_id,
        "status": status,
        "message": message,
        "cost": cost,
    }
