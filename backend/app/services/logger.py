"""
企业级日志服务
- 结构化日志
- 分级记录（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- 按日期轮转
- 异常堆栈追踪
"""
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime


def setup_logger(name: str = "ai_platform", level: str = "INFO") -> logging.Logger:
    """
    设置企业级日志

    Args:
        name: 日志名称
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)

    Returns:
        配置好的 Logger 实例
    """
    # 创建 logs 目录
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # 创建 logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 日志格式
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(filename)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 Handler（按大小轮转，最大 10MB）
    file_handler = RotatingFileHandler(
        log_dir / f"{name}.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 错误日志单独文件
    error_handler = RotatingFileHandler(
        log_dir / f"{name}_error.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    return logger


# 全局 logger 实例
logger = setup_logger()


def get_logger(name: str = "ai_platform") -> logging.Logger:
    """获取 logger 实例"""
    return logging.getLogger(name)


# 便捷的日志方法
def log_info(message: str, **kwargs) -> None:
    """信息日志"""
    if kwargs:
        logger.info(f"{message} | {kwargs}")
    else:
        logger.info(message)


def log_warning(message: str, **kwargs) -> None:
    """警告日志"""
    if kwargs:
        logger.warning(f"{message} | {kwargs}")
    else:
        logger.warning(message)


def log_error(message: str, exc_info: bool = True, **kwargs) -> None:
    """错误日志"""
    if kwargs:
        logger.error(f"{message} | {kwargs}", exc_info=exc_info)
    else:
        logger.error(message, exc_info=exc_info)


def log_debug(message: str, **kwargs) -> None:
    """调试日志"""
    if kwargs:
        logger.debug(f"{message} | {kwargs}")
    else:
        logger.debug(message)
